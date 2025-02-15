import json
import os
import sys

from loguru import logger

_current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.extend([_current_dir])

from src.utils import new_async_client


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
