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
        - visual_queries are fallback topic searches only; final stock footage queries are generated later from the finished facts.
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
        Write a YouTube Shorts package for a spoken "3 facts about X" video that sounds human, natural, and unscripted.

        Topic:
        - Bucket: {topic.bucket}
        - Topic: {topic.topic}

        Constraints:
        - English only
        - Exactly 3 concise, accurate-sounding facts
        - Strong clear title, no emoji
        - Also write title_hook: a more clickable SEO title that does not use "3 Facts About"
        - Prefer title formats like:
          "Deep Sea Vents Shouldn't Exist (But They Do)"
          "The Ocean Has a Secret You've Never Seen"
          "This Is What Lives at 3,000 Meters Down"
          "Saturn Is Stranger Than It Looks"
        - The narration should feel like natural spoken English, not a rigid script
        - Open the narration with a surprising statement or a question
        - Mention {topic.topic} early, but do not force a fixed opener
        - Work the three facts into the narration naturally instead of mechanically listing "Fact 1, Fact 2, Fact 3"
        - Never use "Here are", "First", "Second", "Third", "Fact 1", "Fact 2", or "Fact 3" in the narration
        - Vary sentence length and rhythm
        - Slightly informal phrasing is good, but keep it clean and easy to follow
        - End with impact, not a summary
        - Avoid stock endings or recap lines
        - Do not end with phrases like "That is why..." or "People remember..." or "it looks unusual on screen"
        - No bullet points, stage directions, or narrator-style labels inside the narration
        - Narration length roughly 20-35 seconds (about 45-90 words)
        - No uncertainty phrases
        - No politics, religion, celebrity gossip, explicit content, dangerous advice, or medical claims
        - Avoid title similarity to these recent titles: {excluded}
        - Every JSON field must be filled; never use "" or [] for required fields
        - The facts array must contain exactly 3 complete sentences copied or summarized from the narration
        - Subtitle text must exactly match the spoken narration
        - hook_text must be a punchy 2-second on-screen opener, maximum 9 words
        - Generate at least 10 relevant hashtags
        - Hashtags should start with #
        - Return strict JSON only

        JSON schema:
        {{
          "bucket": "{topic.bucket}",
          "topic": "{topic.topic}",
          "title": "<title>",
          "title_hook": "<attention-grabbing alternative title>",
          "hook_text": "<short visual hook text>",
          "description": "<description>",
          "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5", "#tag6", "#tag7", "#tag8", "#tag9", "#tag10"],
          "narration": "<full narration>",
          "facts": ["<fact 1>", "<fact 2>", "<fact 3>"],
          "subtitle_text": "<subtitle version of narration>"
        }}
        """
    ).strip()
