#!/usr/bini/env python3
import openai, json, os
from duckduckgo_search import DDGS

openai.api_key = os.getenv("OPENAI_API_KEY")

def do_search(query: str, k: int = 5):
    with DDGS() as ddgs:
        hits = [r["body"] for r in ddgs.text(query, max_results=k)]
    return "\n".join(hits)

functions
