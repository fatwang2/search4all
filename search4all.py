import asyncio
import json
import os
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

import httpx
import sanic
from dotenv import load_dotenv
from loguru import logger
from sanic import Sanic
from sanic.exceptions import HTTPException, InvalidUsage

_current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.extend([_current_dir])

from src.cache import KVWrapper
from src.constant import *
from src.qa import get_related_questions
from src.search import (
    search_with_bing,
    search_with_google,
    search_with_search1api,
    search_with_searchapi,
    search_with_searXNG,
    search_with_serper,
)
from src.utils import _raw_stream_response, new_async_client

load_dotenv()
app = Sanic("search")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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
    _app.ctx.executor = ThreadPoolExecutor(
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
    # query = query or default_query
    # Basic attack protection: remove "[INST]" or "[/INST]" from the query
    query = re.sub(r"\[/?INST\]", "", query)
    # 开启聊天历史并且有有效数据 则不再重新请求搜索
    if not _app.ctx.should_do_chat_history or contexts in ("", None):
        contexts = await _app.loop.run_in_executor(
            _app.ctx.executor, _app.ctx.search_function, query
        )

    system_prompt = rag_query_text.format(
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
