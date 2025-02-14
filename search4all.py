import asyncio
import concurrent.futures
import json
import os
import re
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator
from urllib.parse import urlparse

import httpx
import requests
import tldextract
import trafilatura
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI
from trafilatura import bare_extraction

load_dotenv()

import sanic
import sanic.exceptions
from sanic import Sanic
from sanic.exceptions import HTTPException, InvalidUsage
from sqlitedict import SqliteDict

app = Sanic("search")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


################################################################################
# Constant values for the RAG model.
################################################################################

# Search engine related. You don't really need to change this.
BING_SEARCH_V7_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
BING_MKT = "en-US"
GOOGLE_SEARCH_ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"
SERPER_SEARCH_ENDPOINT = "https://google.serper.dev/search"
SEARCHAPI_SEARCH_ENDPOINT = "https://www.searchapi.io/api/v1/search"
SEARCH1API_SEARCH_ENDPOINT = "https://api.search1api.com/search/"


# Specify the number of references from the search engine you want to use.
# 8 is usually a good number.
REFERENCE_COUNT = 8

# Specify the default timeout for the search engine. If the search engine
# does not respond within this time, we will return an error.
DEFAULT_SEARCH_ENGINE_TIMEOUT = 5

# 默认记录的对话历史长度
MAX_HISTORY_LEN = 10


# If the user did not provide a query, we will use this default query.
_default_query = "Who said 'live long and prosper'?"

# This is really the most important part of the rag model. It gives instructions
# to the model on how to generate the answer. Of course, different models may
# behave differently, and we haven't tuned the prompt to make it optimal - this
# is left to you, application creators, as an open problem.
_rag_query_text = """
You are a large language AI assistant built by AI. You are given a user question, and please write clean, concise and accurate answer to the question. You will be given a set of related contexts to the question, each starting with a reference number like [[citation:x]], where x is a number. Please use the context and cite the context at the end of each sentence if applicable.

Your answer must be correct, accurate and written by an expert using an unbiased and professional tone. Please limit to 1024 tokens. Do not give any information that is not related to the question, and do not repeat. Say "information is missing on" followed by the related topic, if the given context do not provide sufficient information.

Please cite the contexts with the reference numbers, in the format [citation:x]. If a sentence comes from multiple contexts, please list all applicable citations, like [citation:3][citation:5]. Other than code and specific names and citations, your answer must be written in the same language as the question.

Here are the set of contexts:

{context}

Remember, don't blindly repeat the contexts verbatim. And here is the user question:
"""

# A set of stop words to use - this is not a complete set, and you may want to
# add more given your observation.
stop_words = [
    "<|im_end|>",
    "[End]",
    "[end]",
    "\nReferences:\n",
    "\nSources:\n",
    "End.",
]

# This is the prompt that asks the model to generate related questions to the
# original question and the contexts.
# Ideally, one want to include both the original question and the answer from the
# model, but we are not doing that here: if we need to wait for the answer, then
# the generation of the related questions will usually have to start only after
# the whole answer is generated. This creates a noticeable delay in the response
# time. As a result, and as you will see in the code, we will be sending out two
# consecutive requests to the model: one for the answer, and one for the related
# questions. This is not ideal, but it is a good tradeoff between response time
# and quality.


class KVWrapper(object):
    def __init__(self, kv_name):
        self._db = SqliteDict(filename=kv_name)

    def get(self, key: str):
        v = self._db[key]
        if v is None:
            raise KeyError(key)
        return v

    def put(self, key: str, value: str):
        self._db[key] = value
        self._db.commit()

    def append(self, key: str, value):
        """记录聊天历史"""
        self._db[key] = self._db.get(key, [])
        # 最长记录的对话轮数 MAX_HISTORY_LEN
        _ = self._db[key][-MAX_HISTORY_LEN:]
        _.append(value)
        self._db[key] = _
        self._db.commit()


# 格式化输出部分
def extract_all_sections(text: str):
    # 定义正则表达式模式以匹配各部分
    sections_pattern = r"(.*?)__LLM_RESPONSE__(.*?)(__RELATED_QUESTIONS__(.*))?$"

    # 使用正则表达式查找各部分内容
    match = re.search(sections_pattern, text, re.DOTALL)

    # 从匹配结果中提取文本，如果没有匹配则返回None
    if match:
        search_results = match.group(1).strip()  # 前置文本作为搜索结果
        llm_response = match.group(2).strip()  # 问题回答部分
        related_questions = (
            match.group(4).strip() if match.group(4) else ""
        )  # 相关问题文本，如果不存在则返回空字符串
    else:
        search_results, llm_response, related_questions = None, None, None

    return search_results, llm_response, related_questions


def search_with_search1api(query: str, search1api_key: str):
    """Search with bing and return the contexts."""
    payload = {"max_results": 10, "query": query, "search_service": "google"}
    headers = {
        "Authorization": f"Bearer {search1api_key}",
        "Content-Type": "application/json",
    }
    response = requests.request(
        "POST", SEARCH1API_SEARCH_ENDPOINT, json=payload, headers=headers
    )
    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException("Search engine error.")

    json_content = response.json()
    try:
        contexts = json_content["results"][:REFERENCE_COUNT]
        for item in contexts:
            item["name"] = item["title"]
            item["url"] = item["link"]
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        return []

    return contexts


def search_with_bing(query: str, subscription_key: str):
    """
    Search with bing and return the contexts.
    """
    params = {"q": query, "mkt": BING_MKT}
    response = requests.get(
        BING_SEARCH_V7_ENDPOINT,
        headers={"Ocp-Apim-Subscription-Key": subscription_key},
        params=params,
        timeout=DEFAULT_SEARCH_ENGINE_TIMEOUT,
    )
    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException("Search engine error.")
    json_content = response.json()
    try:
        contexts = json_content["webPages"]["value"][:REFERENCE_COUNT]
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        return []
    return contexts


def search_with_google(query: str, subscription_key: str, cx: str):
    """
    Search with google and return the contexts.
    """
    params = {
        "key": subscription_key,
        "cx": cx,
        "q": query,
        "num": REFERENCE_COUNT,
    }
    response = requests.get(
        GOOGLE_SEARCH_ENDPOINT, params=params, timeout=DEFAULT_SEARCH_ENGINE_TIMEOUT
    )
    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException("Search engine error.")
    json_content = response.json()
    try:
        contexts = json_content["items"][:REFERENCE_COUNT]
        for item in contexts:
            item["name"] = item["title"]
            item["url"] = item["link"]
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        return []
    return contexts


def search_with_serper(query: str, subscription_key: str):
    """
    Search with serper and return the contexts.
    """
    payload = json.dumps(
        {
            "q": query,
            "num": (
                REFERENCE_COUNT
                if REFERENCE_COUNT % 10 == 0
                else (REFERENCE_COUNT // 10 + 1) * 10
            ),
        }
    )
    headers = {"X-API-KEY": subscription_key, "Content-Type": "application/json"}
    logger.info(
        f"{payload} {headers} {subscription_key} {query} {SERPER_SEARCH_ENDPOINT}"
    )
    response = requests.post(
        SERPER_SEARCH_ENDPOINT,
        headers=headers,
        data=payload,
        timeout=DEFAULT_SEARCH_ENGINE_TIMEOUT,
    )
    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException("Search engine error.")
    json_content = response.json()
    try:
        # convert to the same format as bing/google
        contexts = []
        if json_content.get("knowledgeGraph"):
            url = json_content["knowledgeGraph"].get("descriptionUrl") or json_content[
                "knowledgeGraph"
            ].get("website")
            snippet = json_content["knowledgeGraph"].get("description")
            if url and snippet:
                contexts.append(
                    {
                        "name": json_content["knowledgeGraph"].get("title", ""),
                        "url": url,
                        "snippet": snippet,
                    }
                )
        if json_content.get("answerBox"):
            url = json_content["answerBox"].get("url")
            snippet = json_content["answerBox"].get("snippet") or json_content[
                "answerBox"
            ].get("answer")
            if url and snippet:
                contexts.append(
                    {
                        "name": json_content["answerBox"].get("title", ""),
                        "url": url,
                        "snippet": snippet,
                    }
                )
        contexts += [
            {"name": c["title"], "url": c["link"], "snippet": c.get("snippet", "")}
            for c in json_content["organic"]
        ]
        return contexts[:REFERENCE_COUNT]
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        return []


def search_with_searchapi(query: str, subscription_key: str):
    """
    Search with SearchApi.io and return the contexts.
    """
    payload = {
        "q": query,
        "engine": "google",
        "num": (
            REFERENCE_COUNT
            if REFERENCE_COUNT % 10 == 0
            else (REFERENCE_COUNT // 10 + 1) * 10
        ),
    }
    headers = {
        "Authorization": f"Bearer {subscription_key}",
        "Content-Type": "application/json",
    }
    logger.info(
        f"{payload} {headers} {subscription_key} {query} {SEARCHAPI_SEARCH_ENDPOINT}"
    )
    response = requests.get(
        SEARCHAPI_SEARCH_ENDPOINT,
        headers=headers,
        params=payload,
        timeout=30,
    )
    if not response.ok:
        logger.error(f"{response.status_code} {response.text}")
        raise HTTPException("Search engine error.")
    json_content = response.json()
    try:
        # convert to the same format as bing/google
        contexts = []

        if json_content.get("answer_box"):
            if json_content["answer_box"].get("organic_result"):
                title = (
                    json_content["answer_box"].get("organic_result").get("title", "")
                )
                url = json_content["answer_box"].get("organic_result").get("link", "")
            if json_content["answer_box"].get("type") == "population_graph":
                title = json_content["answer_box"].get("place", "")
                url = json_content["answer_box"].get("explore_more_link", "")

            title = json_content["answer_box"].get("title", "")
            url = json_content["answer_box"].get("link")
            snippet = json_content["answer_box"].get("answer") or json_content[
                "answer_box"
            ].get("snippet")

            if url and snippet:
                contexts.append({"name": title, "url": url, "snippet": snippet})

        if json_content.get("knowledge_graph"):
            if json_content["knowledge_graph"].get("source"):
                url = json_content["knowledge_graph"].get("source").get("link", "")

            url = json_content["knowledge_graph"].get("website", "")
            snippet = json_content["knowledge_graph"].get("description")

            if url and snippet:
                contexts.append(
                    {
                        "name": json_content["knowledge_graph"].get("title", ""),
                        "url": url,
                        "snippet": snippet,
                    }
                )

        contexts += [
            {"name": c["title"], "url": c["link"], "snippet": c.get("snippet", "")}
            for c in json_content["organic_results"]
        ]

        if json_content.get("related_questions"):
            for question in json_content["related_questions"]:
                if question.get("source"):
                    url = question.get("source").get("link", "")
                else:
                    url = ""

                snippet = question.get("answer", "")

                if url and snippet:
                    contexts.append(
                        {
                            "name": question.get("question", ""),
                            "url": url,
                            "snippet": snippet,
                        }
                    )

        return contexts[:REFERENCE_COUNT]
    except KeyError:
        logger.error(f"Error encountered: {json_content}")
        return []


def extract_url_content(url):
    logger.info(url)
    downloaded = trafilatura.fetch_url(url)
    content = trafilatura.extract(downloaded)

    logger.info(url + "______" + content)
    return {"url": url, "content": content}


def search_with_searXNG(query: str, url: str):

    content_list = []

    try:
        safe_string = urllib.parse.quote_plus(":auto " + query)
        response = requests.get(
            url
            + "?q="
            + safe_string
            + "&category=general&format=json&engines=bing%2Cgoogle"
        )
        response.raise_for_status()
        search_results = response.json()

        pedding_urls = []

        conv_links = []

        results = []
        if search_results.get("results"):
            for item in search_results.get("results")[0:9]:
                name = item.get("title")
                snippet = item.get("content")
                url = item.get("url")
                pedding_urls.append(url)

                if url:
                    url_parsed = urlparse(url)
                    domain = url_parsed.netloc
                    icon_url = (
                        url_parsed.scheme + "://" + url_parsed.netloc + "/favicon.ico"
                    )
                    site_name = tldextract.extract(url).domain

                conv_links.append(
                    {
                        "site_name": site_name,
                        "icon_url": icon_url,
                        "title": name,
                        "name": name,
                        "url": url,
                        "snippet": snippet,
                    }
                )

            # executor = ThreadPoolExecutor(max_workers=10)
            # for url in pedding_urls:
            #     futures.append(executor.submit(extract_url_content,url))
            # try:
            #     for future in futures:
            #         res = future.result(timeout=5)
            #         results.append(res)
            # except concurrent.futures.TimeoutError:
            #     logger.error("任务执行超时")
            #     executor.shutdown(wait=False,cancel_futures=True)
            # logger.info(results)
            # for content in results:
            #     if content and content.get('content'):

            #         item_dict = {
            #             "url":content.get('url'),
            #             "name":content.get('url'),
            #             "snippet":content.get('content'),
            #             "content": content.get('content'),
            #             "length":len(content.get('content'))
            #         }
            #         content_list.append(item_dict)
            #     logger.info("URL: {}".format(url))
            #     logger.info("=================")
        if len(results) == 0:
            content_list = conv_links
        return content_list
    except Exception as ex:
        logger.error(ex)
        raise ex


def new_async_client(_app):
    if "claude-3" in _app.ctx.model.lower():
        return AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    else:
        return AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("GROQ_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            http_client=_app.ctx.http_session,
        )


@app.before_server_start
async def server_init(_app):
    """
    Initializes global configs.
    """
    _app.ctx.backend = os.getenv("BACKEND").upper()
    # if _app.ctx.backend == "LEPTON":
    #     from leptonai import Client

    #     _app.ctx.leptonsearch_client = Client(
    #         "https://search-api.lepton.run/",
    #         token=os.getenv.get("LEPTON_WORKSPACE_TOKEN"),
    #         stream=True,
    #         timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10),
    #     )
    if _app.ctx.backend == "BING":
        _app.ctx.search_api_key = os.getenv("BING_SEARCH_V7_SUBSCRIPTION_KEY")
        _app.ctx.search_function = lambda query: search_with_bing(
            query,
            _app.ctx.search_api_key,
        )
    elif _app.ctx.backend == "GOOGLE":
        _app.ctx.search_api_key = os.getenv("GOOGLE_SEARCH_API_KEY")
        _app.ctx.search_function = lambda query: search_with_google(
            query,
            _app.ctx.search_api_key,
            os.getenv("GOOGLE_SEARCH_CX"),
        )
    elif _app.ctx.backend == "SERPER":
        _app.ctx.search_api_key = os.getenv("SERPER_SEARCH_API_KEY")
        _app.ctx.search_function = lambda query: search_with_serper(
            query,
            _app.ctx.search_api_key,
        )
    elif _app.ctx.backend == "SEARCHAPI":
        _app.ctx.search_api_key = os.getenv("SEARCHAPI_API_KEY")
        _app.ctx.search_function = lambda query: search_with_searchapi(
            query,
            _app.ctx.search_api_key,
        )
    elif _app.ctx.backend == "SEARCH1API":
        _app.ctx.search1api_key = os.getenv("SEARCH1API_KEY")
        _app.ctx.search_function = lambda query: search_with_search1api(
            query,
            _app.ctx.search1api_key,
        )
    elif _app.ctx.backend == "SEARXNG":
        logger.info(os.getenv("SEARXNG_BASE_URL"))
        _app.ctx.search_function = lambda query: search_with_searXNG(
            query,
            os.getenv("SEARXNG_BASE_URL"),
        )
    else:
        raise RuntimeError(
            "Backend must be BING, GOOGLE, SERPER or SEARCHAPI or SEARCH1API."
        )
    _app.ctx.model = os.getenv("LLM_MODEL")
    _app.ctx.handler_max_concurrency = 16
    # An executor to carry out async tasks, such as uploading to KV.
    _app.ctx.executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=_app.ctx.handler_max_concurrency * 2
    )
    # Create the KV to store the search results.
    logger.info("Creating KV. May take a while for the first time.")
    _app.ctx.kv = KVWrapper(os.getenv("KV_NAME") or "search.db")
    # whether we should generate related questions.
    _app.ctx.should_do_related_questions = bool(
        os.getenv("RELATED_QUESTIONS") in ("1", "yes", "true")
    )
    _app.ctx.should_do_chat_history = bool(
        os.getenv("CHAT_HISTORY") in ("1", "yes", "true")
    )
    # Create httpx Session
    _app.ctx.http_session = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10),
    )


async def get_related_questions(_app, query, contexts):
    """
    Gets related questions based on the query and context.
    """
    _more_questions_prompt = r"""
    You are a helpful assistant that helps the user to ask related questions, based on user's original question and the related contexts. Please identify worthwhile topics that can be follow-ups, and write questions no longer than 20 words each. Please make sure that specifics, like events, names, locations, are included in follow up questions so they can be asked standalone. For example, if the original question asks about "the Manhattan project", in the follow up question, do not just say "the project", but use the full name "the Manhattan project". Your related questions must be in the same language as the original question.

    Here are the contexts of the question:

    {context}

    Remember, based on the original question and related contexts, suggest three such further questions. Do NOT repeat the original question. Each related question should be no longer than 20 words. Here is the original question:
    """.format(
        context="\n\n".join([c["snippet"] for c in contexts])
    )

    try:
        logger.info("Start getting related questions")
        if "claude-3" in _app.ctx.model.lower():
            logger.info("Using Claude-3 model")
            client = new_async_client(_app)
            tools = [
                {
                    "name": "ask_related_questions",
                    "description": "Get a list of questions related to the original question and context.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "questions": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "description": "A related question to the original question and context.",
                                },
                            }
                        },
                        "required": ["questions"],
                    },
                }
            ]
            response = await client.beta.tools.messages.create(
                model=_app.ctx.model,
                system=_more_questions_prompt,
                max_tokens=1000,
                tools=tools,
                messages=[
                    {"role": "user", "content": query},
                ],
            )
            logger.info("Response received from Claude-3 model")

            if response.content and len(response.content) > 0:
                related = []
                for block in response.content:
                    if (
                        block.type == "tool_use"
                        and block.name == "ask_related_questions"
                    ):
                        related = block.input["questions"]
                        break
            else:
                related = []

            if related and isinstance(related, str):
                try:
                    related = json.loads(related)
                except json.JSONDecodeError:
                    logger.error("Failed to parse related questions as JSON")
                    return []
            logger.info("Successfully got related questions")
            return [{"question": question} for question in related[:5]]
        else:
            logger.info("Using OpenAI model")
            openai_client = new_async_client(_app)
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "ask_related_questions",
                        "description": "Get a list of questions related to the original question and context.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "questions": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "description": "A related question to the original question and context.",
                                    },
                                }
                            },
                            "required": ["questions"],
                        },
                    },
                }
            ]
            messages = [
                {"role": "system", "content": _more_questions_prompt},
                {"role": "user", "content": query},
            ]
            request_body = {
                "model": _app.ctx.model,
                "messages": messages,
                "max_tokens": 1000,
                "tools": tools,
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "ask_related_questions"},
                },
            }
            try:
                llm_response = await openai_client.chat.completions.create(
                    **request_body
                )

                if llm_response.choices and llm_response.choices[0].message:
                    message = llm_response.choices[0].message

                    if message.tool_calls:
                        related = message.tool_calls[0].function.arguments
                        if isinstance(related, str):
                            related = json.loads(related)
                        logger.trace(f"Related questions: {related}")
                        return [
                            {"question": question}
                            for question in related["questions"][:5]
                        ]

                    elif message.content:
                        # 如果不存在 tool_calls 字段,但存在 content 字段,从 content 中提取相关问题
                        content = message.content
                        related_questions = content.split("\n")
                        related_questions = [
                            q.strip() for q in related_questions if q.strip()
                        ]

                        # 提取带有序号的问题
                        cleaned_questions = []
                        for question in related_questions:
                            if (
                                question.startswith("1.")
                                or question.startswith("2.")
                                or question.startswith("3.")
                            ):
                                question = question[3:].strip()  # 去除问题编号和空格

                                if question.startswith('"') and question.endswith('"'):
                                    question = question[1:-1]  # 去除首尾的双引号
                                elif question.startswith('"'):
                                    question = question[1:]  # 去除开头的双引号
                                elif question.endswith('"'):
                                    question = question[:-1]  # 去除结尾的双引号

                                cleaned_questions.append(question)

                        logger.trace(f"Related questions: {cleaned_questions}")
                        return [
                            {"question": question} for question in cleaned_questions[:5]
                        ]

            except Exception as e:
                logger.error(
                    f"Error occurred while sending request to OpenAI model: {str(e)}"
                )
                return []
    except Exception as e:
        logger.error(f"Encountered error while generating related questions: {str(e)}")
        return []


async def _raw_stream_response(
    _app, contexts, llm_response, related_questions_future
) -> AsyncGenerator[str, None]:
    """
    A generator that yields the raw stream response. You do not need to call
    this directly. Instead, use the stream_and_upload_to_kv which will also
    upload the response to KV.
    """
    # First, yield the contexts.
    yield json.dumps(contexts)
    yield "\n\n__LLM_RESPONSE__\n\n"
    # Second, yield the llm response.
    if not contexts:
        # Prepend a warning to the user
        yield (
            "(The search engine returned nothing for this query. Please take the"
            " answer with a grain of salt.)\n\n"
        )

    if "claude-3" in _app.ctx.model.lower():
        # Process Claude's stream response
        async for text in llm_response:
            yield text
    else:
        # Process OpenAI's stream response
        async for chunk in llm_response:
            if chunk.choices:
                yield chunk.choices[0].delta.content or ""
    # Third, yield the related questions. If any error happens, we will just
    # return an empty list.
    if related_questions_future is not None:
        related_questions = await related_questions_future
        try:
            result = json.dumps(related_questions)
        except Exception as e:
            logger.error(f"encountered error: {e}\n{traceback.format_exc()}")
            result = "[]"
        yield "\n\n__RELATED_QUESTIONS__\n\n"
        yield result


def get_query_object(request):
    params = {k: v[0] for k, v in request.args.items()}
    if request.method == "POST":
        if "form" in request.content_type:
            params.update({k: v[0] for k, v in request.form.items()})
        else:
            try:
                if request.json:
                    params.update(request.json)
            except InvalidUsage:
                pass
    return params


@app.route("/query", methods=["POST"])
async def query_function(request: sanic.Request):
    """
    Query the search engine and returns the response.

    The query can have the following fields:
        - query: the user query.
        - search_uuid: a uuid that is used to store or retrieve the search result. If
            the uuid does not exist, generate and write to the kv. If the kv
            fails, we generate regardless, in favor of availability. If the uuid
            exists, return the stored result.
        - generate_related_questions: if set to false, will not generate related
            questions. Otherwise, will depend on the environment variable
            RELATED_QUESTIONS. Default: true.
    """
    _app = request.app
    params = get_query_object(request)
    query = params.get("query", None)
    search_uuid = params.get("search_uuid", None)
    generate_related_questions = params.get("generate_related_questions", True)
    if not query:
        raise HTTPException("query must be provided.")

    # 定义传递给生成答案的聊天历史 以及搜索结果
    chat_history = []
    contexts = ""

    # Note that, if uuid exists, we don't check if the stored query is the same
    # as the current query, and simply return the stored result. This is to enable
    # the user to share a searched link to others and have others see the same result.
    if search_uuid:
        if _app.ctx.should_do_chat_history:
            # 开启了历史记录，读取历史记录
            history = []
            try:
                history = await _app.loop.run_in_executor(
                    _app.ctx.executor,
                    lambda sid: _app.ctx.kv.get(sid),
                    f"{search_uuid}_history",
                )
                result = await _app.loop.run_in_executor(
                    _app.ctx.executor, lambda sid: _app.ctx.kv.get(sid), search_uuid
                )
                # return sanic.text(result)
            except KeyError:
                logger.info(f"Key {search_uuid} not found, will generate again.")
            except Exception as e:
                logger.error(
                    f"KV error: {e}\n{traceback.format_exc()}, will generate again."
                )
            # 如果存在历史记录
            if history:
                # 获取最后一次记录
                last_entry = history[-1]
                # 确定最后一次记录的数据完整性
                old_query, search_results, llm_response = (
                    last_entry.get("query", ""),
                    last_entry.get("search_results", ""),
                    last_entry.get("llm_response", ""),
                )
                # 如果存在旧查询和搜索结果
                if old_query and search_results:
                    if old_query != query:
                        # 从历史记录中获取搜索结果（最后一条）
                        contexts = history[-1]["search_results"]
                        # 将历史聊天的提问和回答提取
                        chat_history = []
                        for entry in history:
                            if "query" in entry and "llm_response" in entry:
                                chat_history.append(
                                    {"role": "user", "content": entry["query"]}
                                )
                                chat_history.append(
                                    {
                                        "role": "assistant",
                                        "content": entry["llm_response"],
                                    }
                                )
                    else:
                        return sanic.text(result["txt"])  # 查询未改变，直接返回结果
        else:
            try:
                result = await _app.loop.run_in_executor(
                    _app.ctx.executor, lambda sid: _app.ctx.kv.get(sid), search_uuid
                )
                # debug
                if isinstance(result, dict):
                    # 只有相同的查询才返回同一个结果， 兼容多轮对话。
                    if result["query"] == query:
                        return sanic.text(result["txt"])
                else:
                    # TODO: 兼容旧数据代码 之后删除
                    # 旧数据强制刷新
                    # return sanic.text(result)
                    pass
            except KeyError:
                logger.info(f"Key {search_uuid} not found, will generate again.")
            except Exception as e:
                logger.error(
                    f"KV error: {e}\n{traceback.format_exc()}, will generate again."
                )
    else:
        raise HTTPException("search_uuid must be provided.")

    # if _app.ctx.backend == "LEPTON":
    #     # delegate to the lepton search api.
    #     result = _app.ctx.leptonsearch_client.query(
    #         query=query,
    #         search_uuid=search_uuid,
    #         generate_related_questions=generate_related_questions,
    #     )
    #     return StreamingResponse(content=result, media_type="text/html")

    # First, do a search query.
    # query = query or _default_query
    # Basic attack protection: remove "[INST]" or "[/INST]" from the query
    query = re.sub(r"\[/?INST\]", "", query)
    # 开启聊天历史并且有有效数据 则不再重新请求搜索
    if not _app.ctx.should_do_chat_history or contexts in ("", None):
        contexts = await _app.loop.run_in_executor(
            _app.ctx.executor, _app.ctx.search_function, query
        )

    system_prompt = _rag_query_text.format(
        context="\n\n".join(
            [f"[[citation:{i+1}]] {c['snippet']}" for i, c in enumerate(contexts)]
        )
    )
    try:
        if _app.ctx.should_do_related_questions and generate_related_questions:
            # While the answer is being generated, we can start generating
            # related questions as a future.
            related_questions_future = get_related_questions(_app, query, contexts)
        if "claude-3" in _app.ctx.model.lower():
            logger.info("Using Claude for generating LLM response")
            client = new_async_client(_app)
            messages = [
                {"role": "user", "content": query},
            ]
            messages = []
            if chat_history:
                messages.extend(chat_history)  # 将历史记录添加到列表开头
            # 然后添加当前查询消息
            messages.append({"role": "user", "content": query})
            response = await request.respond(content_type="text/html")
            all_yielded_results = []

            # First, yield the contexts.
            logger.info("Sending initial context and LLM response marker.")
            context_str = json.dumps(contexts)
            await response.send(context_str)
            all_yielded_results.append(context_str)
            await response.send("\n\n__LLM_RESPONSE__\n\n")
            all_yielded_results.append("\n\n__LLM_RESPONSE__\n\n")

            # Second, yield the llm response.
            if not contexts:
                warning = "(The search engine returned nothing for this query. Please take the answer with a grain of salt.)\n\n"
                await response.send(warning)
                all_yielded_results.append(warning)
            if related_questions_future is not None:
                related_questions_task = asyncio.create_task(related_questions_future)
            async with client.messages.stream(
                model=_app.ctx.model,
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    all_yielded_results.append(text)
                    await response.send(text)

            logger.info("Finished streaming LLM response")
            # 在生成回复的同时异步等待相关问题任务完成
            if related_questions_future is not None:
                try:
                    logger.info("About to send related questions.")
                    related_questions = await related_questions_task
                    logger.info("Related questions sent.")
                    result = json.dumps(related_questions)
                    await response.send("\n\n__RELATED_QUESTIONS__\n\n")
                    all_yielded_results.append("\n\n__RELATED_QUESTIONS__\n\n")
                    await response.send(result)
                    all_yielded_results.append(result)
                except Exception as e:
                    logger.error(f"Error during related questions generation: {e}")

        else:
            logger.info("Using OpenAI for generating LLM response")
            openai_client = new_async_client(_app)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ]

            if chat_history and len(chat_history) % 2 == 0:
                # 将历史插入到消息中 index = 1 的位置
                messages[1:1] = chat_history
            llm_response = await openai_client.chat.completions.create(
                model=_app.ctx.model,
                messages=messages,
                max_tokens=1024,
                stream=True,
                temperature=0.9,
            )
            response = await request.respond(content_type="text/html")
            # First, stream and yield the results.
            all_yielded_results = []
            async for result in _raw_stream_response(
                _app, contexts, llm_response, related_questions_future
            ):
                all_yielded_results.append(result)
                await response.send(result)
            logger.info("Finished streaming LLM response")

    except Exception as e:
        logger.error(f"encountered error: {e}\n{traceback.format_exc()}")
        return sanic.json({"message": "Internal server error."}, 503)
    # Second, upload to KV. Note that if uploading to KV fails, we will silently
    # ignore it, because we don't want to affect the user experience.
    await response.eof()
    if _app.ctx.should_do_chat_history:
        # 保存聊天历史
        _search_results, _llm_response, _related_questions = (
            await _app.loop.run_in_executor(
                _app.ctx.executor, extract_all_sections, "".join(all_yielded_results)
            )
        )
        if _search_results:
            _search_results = json.loads(_search_results)
        if _related_questions:
            _related_questions = json.loads(_related_questions)
        _ = _app.ctx.executor.submit(
            _app.ctx.kv.append,
            f"{search_uuid}_history",
            {
                "query": query,
                "search_results": _search_results,
                "llm_response": _llm_response,
                "related_questions": _related_questions,
            },
        )
    _ = _app.ctx.executor.submit(
        _app.ctx.kv.put,
        search_uuid,
        {
            "query": query,
            "txt": "".join(all_yielded_results),
        },  # 原来的缓存是直接根据sid返回结果，开启聊天历史后 同一个sid存储多轮对话，因此需要存储 query 兼容多轮对话
    )


app.static("/ui", os.path.join(BASE_DIR, "ui/"), name="/")
app.static("/", os.path.join(BASE_DIR, "ui/index.html"), name="ui")


if __name__ == "__main__":
    port = int(os.getenv("PORT") or 8800)
    workers = int(os.getenv("WORKERS") or 1)
    app.run(host="0.0.0.0", port=port, workers=workers, debug=False)
