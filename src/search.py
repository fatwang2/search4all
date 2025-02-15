import json
import logging
import os
import sys
from urllib.parse import quote_plus, urlparse

import requests
import tldextract
from sanic.exceptions import HTTPException, InvalidUsage
from trafilatura import bare_extraction, extract, fetch_url

_current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.extend([_current_dir])

from src.constant import *

logger = logging.getLogger("search")


def extract_url_content(url):
    logger.info(url)
    downloaded = fetch_url(url)
    content = extract(downloaded)

    logger.info(url + "______" + content)
    return {"url": url, "content": content}


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


def search_with_searXNG(query: str, url: str):

    content_list = []

    try:
        safe_string = quote_plus(":auto " + query)
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
