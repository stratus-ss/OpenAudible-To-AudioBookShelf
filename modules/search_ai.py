import json
from typing import Dict, Any

from openai import OpenAI


def search_open_ai(book_title: str, api_key: str) -> Dict[str, Any]:
    """Searches for book information using OpenAI API.

    Args:
        book_title: The title of the book to search for.
        api_key: The OpenAI API key.

    Returns:
        A json (dictionary) data structure containing book information.
    """

    client = OpenAI(api_key=api_key)
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant that returns structured JSON data only, without additional explanations.",
        },
        {
            "role": "user",
            "content": f"""
        Search for the book "{book_title}" and return only JSON with these fields:
        {{
            "Author": "",
            "Book Title": "",
            "Book Series": "",
            "Book Sequence Number": "",
            "Book Image Thumbnail": ""
        }}
        """,
        },
    ]

    response = client.chat.completions.create(
        model="gpt-4",  # or "gpt-3.5-turbo"
        messages=messages,
        max_tokens=300,
        temperature=0,
    )

    reply = response.choices[0].message.content

    try:
        result = json.loads(reply.strip())
        return result
    except json.JSONDecodeError:
        return {"error": "Failed to parse JSON from API response"}


def search_perplexity(book_title: str, api_key: str) -> Dict[str, Any]:
    """Searches for book information using Perplexity AI API.

    Args:
        book_title: The title of the book to search for.
        api_key: The Perplexity AI API key.

    Returns:
        A json (dictionary) data structure containing book information.
    """
    import requests
    from pydantic import BaseModel

    class AnswerFormat(BaseModel):
        author: str
        book_title: str
        book_sequence_number: int
        book_series_title: str

    url = "https://api.perplexity.ai/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "sonar",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that returns structured JSON data only, without additional explanations.",
            },
            {
                "role": "user",
                "content": (
                    f"Search for the book {book_title} and return only JSON with these fields:"
                    "author, book_title, book_sequence_number, book_series_title"
                    "Do not format the output at all. Simply return the structured JSON data"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"schema": AnswerFormat.model_json_schema()},
        },
    }
    response = requests.post(url, headers=headers, json=payload).json()
    return json.loads(response["choices"][0]["message"]["content"])
