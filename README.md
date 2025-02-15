<div align="center">
<h1 align="center">Search4All</h1>
The opensource AI search platform
<br/>
<a href="https://x.com/fatwang2ai/status/1771507973418049598" target="_blank"> Live Demo </a>
<br/>
<img width="70%" src="https://github.com/leptonai/search_with_lepton/assets/1506722/845d7057-02cd-404e-bbc7-60f4bae89680">
</div>

## About me

[X](https://sum4all.site/twitter)\
[telegram](https://sum4all.site/telegram)

<a href="https://www.buymeacoffee.com/fatwang2" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>

## Features

- Integrated support for LLMs, such as OpenAI, Groq, and Claude.
- Native search engine integration, including Google, Bing, DuckDuckGo and SearXNG Search.
- Customizable, visually appealing user interface.
- Shareable and cached search results for enhanced efficiency.

## Setup Search Engine API

Choose your search service

### Search1API

Search1API is a versatile search aggregation service that enables you to perform searches across Google, Bing, and DuckDuckGo, and also retrieve clear content from URLs. [search1api website](https://go.search1api.com/homepage)

### Bing Search

To use the Bing Web Search API, please visit [this link](https://www.microsoft.com/en-us/bing/apis/bing-web-search-api) to obtain your Bing subscription key.

### Google Search

You have three options for Google Search: you can use the [SearchApi Google Search API](https://www.searchapi.io/) from SearchApi, [Serper Google Search API](https://www.serper.dev) from Serper, or opt for the [Programmable Search Engine](https://developers.google.com/custom-search) provided by Google.

### SearXNG Search

you can host your personal [SearXNG server](https://github.com/searxng/searxng), then you do not need pay for the search api. You just need provide the server address in `SEARXNG_BASE_URL`, plz be sure you enable the json format for the SearXNG server.

## Deployment

### Zeabur

Just click on it

<a href="https://zeabur.com/templates/YHKPET?referralCode=fatwang2"><img src="https://zeabur.com/button.svg" alt="Deploy on Zeabur"/></a>

### Docker

Change the environment variables and run the docker

```
docker run -d --name search4all -e OPENAI_API_KEY=sk-XXX -e OPENAI_BASE_URL=https://api.openai.com/v1 -e LLM_MODEL=gpt-3.5-turbo-0125 -e RELATED_QUESTIONS=1 -e SEARCH1API_KEY=XXX -e BACKEND=SEARCH1API -p 8800:8800 docker.io/fatwang2/search4all
```

### Docker-Compose

1. Download the docker-compose file on your mechine

```
wget https://raw.githubusercontent.com/fatwang2/search4all/main/docker-compose.yml
```

2. Change the environment variables in the file

3. Run the docker

```
docker compose up -d
```

### Manual

1. install the requirements.txt

```shell
pip3 install -r requirements.txt
```

2. Set you LLM

```shell
export OPENAI_API_KEY=sk-XXX
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-3.5-turbo-0125
RELATED_QUESTIONS=1
NODE_ENV=production
```

3. Set your key of search

```shell
export SEARCH1API_KEY=YOUR_SEARCH1API_KEY
```

4. Build web

```shell
cd web && npm install && npm run build
```

5. Run server

```shell
BACKEND=SEARCH1API python3 search4all.py
```

## Environment Variable

This project provides some additional configuration items set with environment variables:

| Environment Variable              | Required | Description                                                                                                                                   | Example                                                                        |
| --------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | --- |
| `OPENAI_API_KEY`                  | Yes      | This is the API key you apply on the OpenAI account page                                                                                      | `sk-xxxxxx...xxxxxx`                                                           |
| `OPENAI_BASE_URL`                 | No       | If you manually configure the OpenAI interface proxy, you can use this configuration item to override the default OpenAI API request base URL | OpenAI: `https://api.openai.com/v1`<br/>Groq: `https://api.groq.com/openai/v1` |     |
| `GROQ_API_KEY`                    | No       | This is the API key you apply on the Groq account page                                                                                        | `gsk_xxxxxx...xxxxxx`                                                          |
| `ANTHROPIC_API_KEY`               | No       | This is the API key you apply on the Claude account page                                                                                      | `sk-ant-xxxxxx...xxxxxx`                                                       |
| `LLM_MODEL`                       | Yes      | The model you want to use,support all chat models of openai, groq and claude.                                                                 | `gpt-3.5-turbo-0125,mixtral-8x7b-32768,claude-3-haiku-20240307...`             |
| `RELATED_QUESTIONS`               | No       | Show the related questions.                                                                                                                   | `1` or `true`                                                                  |
| `NODE_ENV`                        | No       | The environment required for deployment is necessary only during manual deployment.                                                           | `production`                                                                   |
| `BACKEND`                         | Yes      | The search service you want.                                                                                                                  | `SEARCH1API,BING,GOOGLE,SERPER,SEARCHAPI,SEARXNG`                              |
| `CHAT_HISTORY`                    | No       | Continue to ask about the results                                                                                                             | `1`                                                                            |
| `SEARCH1API_KEY`                  | Yes      | If you choose SEARCH1API.                                                                                                                     | `xxx`                                                                          |
| `BING_SEARCH_V7_SUBSCRIPTION_KEY` | No       | If you choose BING.                                                                                                                           | `xxx`                                                                          |
| `GOOGLE_SEARCH_CX`                | No       | If you choose GOOGLE.                                                                                                                         | `xxxx`                                                                         |
| `GOOGLE_SEARCH_API_KEY`           | No       | If you choose GOOGLE.                                                                                                                         | `xxx`                                                                          |
| `SEARCHAPI_API_KEY`               | No       | If you choose SEARCHAPI.                                                                                                                      | `xxx`                                                                          |
| `SERPER_SEARCH_API_KEY`           | No       | If you choose SERPER.                                                                                                                         | `xxx`                                                                          |
| `NEXT_PUBLIC_GOOGLE_ANALYTICS`    | No       | You can use Google Analytics to know how many users you have on your website.                                                                 | MEASUREMENT ID,you can find on your google analytics account,like `G-XXXXXX`   |
| `SEARXNG_BASE_URL`                | No       | the hosted serxng server address. it is required when the BACKEND is `SEARXNG`                                                                | `https://serxng.xxx.com/`                                                      |

## TODO

- [ ] Support Lepton
- [ ] Support continuous search
- [ ] Support More LLMs
- [x] Support Searxng Search
- [x] Support Claude
- [x] Support Groq
- [x] Support back to home when searching
- [x] Support continuous talk about the results
- [x] Support Google Analytics
- [x] Support the related questions by function calling
- [x] Support the Docker
- [x] Support the Docker-Compose
- [x] Support the Zeabur
