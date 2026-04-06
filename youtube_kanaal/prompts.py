from __future__ import annotations

from textwrap import dedent

from youtube_kanaal.models.content import TOPIC_CATALOG, TopicChoice


def build_topic_selection_prompt(excluded_topics: list[str]) -> str:
    catalog_lines = []
    for bucket, topics in TOPIC_CATALOG.items():
        catalog_lines.append(f"- {bucket}: {', '.join(topics)}")
    excluded_line = ", ".join(excluded_topics[-20:]) if excluded_topics else "None"
    return dedent(
        f"""
        You are generating safe, visual YouTube Shorts topics.

        Choose exactly one topic from this curated catalog:
        {chr(10).join(catalog_lines)}

        Constraints:
        - Choose from the catalog only.
        - Avoid recent topics: {excluded_line}
        - Pick something visually rich and broad enough for stock footage.
        - Return strict JSON only.

        JSON schema:
        {{
          "bucket": "<allowed bucket>",
          "topic": "<catalog topic>",
          "visual_queries": ["<query 1>", "<query 2>", "<query 3>"],
          "search_terms": ["<term 1>", "<term 2>", "<term 3>"]
        }}
        """
    ).strip()


def build_content_generation_prompt(topic: TopicChoice, excluded_titles: list[str]) -> str:
    excluded = ", ".join(excluded_titles[-20:]) if excluded_titles else "None"
    return dedent(
        f"""
        Write a YouTube Shorts package for the format "3 facts about X".

        Topic:
        - Bucket: {topic.bucket}
        - Topic: {topic.topic}

        Constraints:
        - English only
        - Exactly 3 concise, accurate-sounding facts
        - Strong clear title, no emoji
        - The narration must open with: "Here are 3 facts about {topic.topic}."
        - Then present Fact 1, Fact 2, and Fact 3 clearly
        - Narration length roughly 20-35 seconds
        - No uncertainty phrases
        - No politics, religion, celebrity gossip, explicit content, dangerous advice, or medical claims
        - Avoid title similarity to these recent titles: {excluded}
        - Subtitle text must exactly match the spoken narration
        - Hashtags should start with #
        - Return strict JSON only

        JSON schema:
        {{
          "bucket": "{topic.bucket}",
          "topic": "{topic.topic}",
          "title": "<title>",
          "description": "<description>",
          "hashtags": ["#tag1", "#tag2", "#tag3"],
          "narration": "<full narration>",
          "facts": ["<fact 1>", "<fact 2>", "<fact 3>"],
          "subtitle_text": "<subtitle version of narration>"
        }}
        """
    ).strip()
