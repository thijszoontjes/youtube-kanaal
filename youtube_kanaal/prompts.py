from __future__ import annotations

from textwrap import dedent

from youtube_kanaal.models.content import TOPIC_CATALOG, TopicChoice


_SHORT_STORY_STYLES = (
    "myth-buster: open with a common belief, then overturn it with visible proof",
    "impossible contrast: open with two facts that seem unable to both be true",
    "mini mystery: show the strange result first, then reveal the cause",
    "record chase: open with the strongest number, then explain what makes it possible",
)


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
        - Pick a topic with one clear surprise, contradiction, mystery, comparison, or visible transformation.
        - The topic must be recognizable in the first second and have literal visual proof available as stock footage.
        - Reject topics that would require generic unrelated b-roll to explain.
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
    variation_seed = sum(ord(char) for char in f"{topic.topic}|{excluded}".lower())
    story_style = _SHORT_STORY_STYLES[variation_seed % len(_SHORT_STORY_STYLES)]
    return dedent(
        f"""
        Write a YouTube Shorts package that feels like one fast, satisfying mini-story told by a curious human.

        Topic:
        - Bucket: {topic.bucket}
        - Topic: {topic.topic}
        - Story shape for this Short: {story_style}

        Constraints:
        - English only
        - Exactly 3 concise, concrete facts that act as evidence inside one story; never present them as a list
        - Strong curiosity title, no emoji
        - The title must match {topic.topic}; do not use another catalog topic or unrelated bait
        - Make the title feel clickable and a little clickbait, but do not make false claims
        - Do not use bland title shapes like "X Wonders", "X Explained", or "3 Facts About X"
        - Use full ALL CAPS for some titles, and use ALL CAPS emphasis words in others; do not make every title all caps
        - Also write title_hook: a bolder clickable SEO title that does not use "3 Facts About"
        - Prefer title formats like:
          "DEEP SEA VENTS SHOULD NOT EXIST"
          "The Ocean Secret Nobody Talks About"
          "This Lives 3,000 Meters Down"
          "SATURN IS HIDING SOMETHING WEIRD"
          "Do NOT Ignore This About Axolotls"
        - Build one promise through exactly four beats: hook, evidence, reversal, payoff. Add one optional loop beat only when it creates a genuinely satisfying return to the hook
        - The spoken hook must be 5-12 words and work in the first second: name or unmistakably identify {topic.topic}, state a concrete surprise or contradiction, and open a precise information gap
        - Do not spend the hook establishing atmosphere; put the subject and surprising claim first
        - Make every later beat add new information: the evidence beat proves the claim, the reversal changes how the viewer sees it, and the payoff answers or reframes the hook
        - Reveal useful proof in the evidence beat, then save the strongest reframe for the payoff instead of withholding every answer until the end
        - The final payoff or loop must be 6-12 spoken words, directly answer or reframe the hook, and end immediately after the strongest idea
        - Never end with a recap, moral, generic importance statement, disclaimer, or production comment
        - The narration should feel like natural spoken English, with contractions, purposeful rhythm, and varied sentence length
        - Spoken narration must use normal sentence capitalization; never write narration in ALL CAPS
        - Do not fake stutters, mistakes, self-corrections, or filler words to imitate a human
        - Put a short pause before the strongest reveal by ending the previous beat cleanly
        - Do not open with "Did you know", "Imagine a world", "Have you ever wondered", or the exact title
        - Work the three facts into the narration naturally instead of mechanically listing "Fact 1, Fact 2, Fact 3"
        - Never use "Here are", "First", "Second", "Third", "Fact 1", "Fact 2", or "Fact 3" in the narration
        - Vary sentence length and rhythm
        - Slightly informal phrasing is good, but keep it clean and easy to follow
        - End with impact or a clean loop, not a summary; do not explain the reveal again afterward
        - Avoid stock endings or recap lines
        - Do not end with phrases like "That is why..." or "People remember..." or "it looks unusual on screen"
        - No bullet points, stage directions, or narrator-style labels inside the narration
        - Narration length roughly 20-35 seconds (about 55-90 words)
        - Description must be 1-2 specific sentences about this exact Short; never leave it blank
        - The facts array must contain exactly 3 complete, concrete facts, not generic video-production statements
        - The facts must support the title and narration
        - Every fact must be explicitly stated or clearly paraphrased in the narration
        - No uncertainty phrases
        - No politics, religion, celebrity gossip, explicit content, dangerous advice, or medical claims
        - Avoid title similarity to these recent titles: {excluded}
        - Every JSON field must be filled; never use "" or [] for required fields
        - The facts array must contain exactly 3 complete sentences copied or summarized from the narration
        - Subtitle text must exactly match the spoken narration
        - Every beat needs a literal, Pexels-friendly visual_query describing the exact subject, action, scale, or comparison that proves that spoken line
        - Write visual_query so the beat can support a primary proof shot and a distinct cutaway or detail shot; prefer visible action over static scenery
        - Never request a scientist, laboratory, crowd, or generic landscape as a proxy for a fact unless that person or place is literally discussed
        - Every beat needs concrete on_screen_text of 2-4 words and at most 24 characters; use a number, mechanism, contrast, or answer that adds information
        - Never use vague overlays such as "Scientific Findings", "Hidden Truth", "Learn More", "Understanding X", or "The Secret Life"
        - visual_query must prioritize the exact subject and visible action before style words
        - energy controls delivery and editing; transition and sfx must support meaning rather than decorate every cut
        - High-energy beats should be visually changeable: include a clear motion, reveal, comparison, number, texture, or close-up that can survive a fast cut
        - narration must exactly equal all beat narration fields joined with single spaces
        - subtitle_text must exactly match narration
        - Generate at least 10 relevant hashtags
        - Hashtags should start with #
        - Return strict JSON only

        JSON schema:
        {{
          "bucket": "{topic.bucket}",
          "topic": "{topic.topic}",
          "title": "<title>",
          "title_hook": "<clickbait-curiosity alternative title>",
          "description": "<description>",
          "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5", "#tag6", "#tag7", "#tag8", "#tag9", "#tag10"],
          "narration": "<full narration>",
          "facts": ["<fact 1>", "<fact 2>", "<fact 3>"],
          "subtitle_text": "<exact narration>",
          "beats": [
            {{
              "beat_type": "hook",
              "narration": "<spoken hook phrase>",
              "on_screen_text": "<2-5 word visual promise>",
              "visual_query": "<literal subject and action>",
              "energy": "high",
              "transition": "punch",
              "sfx": "impact",
              "duration_weight": 0.7
            }},
            {{
              "beat_type": "evidence",
              "narration": "<spoken proof phrase>",
              "on_screen_text": "<2-5 proof words>",
              "visual_query": "<literal proof subject and action>",
              "energy": "medium",
              "transition": "cut",
              "sfx": "tick",
              "duration_weight": 1.0
            }},
            {{
              "beat_type": "escalation",
              "narration": "<spoken reversal phrase>",
              "on_screen_text": "<2-5 reversal words>",
              "visual_query": "<literal reversal subject and action>",
              "energy": "high",
              "transition": "punch",
              "sfx": "riser",
              "duration_weight": 1.0
            }},
            {{
              "beat_type": "payoff",
              "narration": "<spoken answer to the hook>",
              "on_screen_text": "<2-5 payoff words>",
              "visual_query": "<literal payoff subject and action>",
              "energy": "high",
              "transition": "hold",
              "sfx": "silence",
              "duration_weight": 1.0
            }}
          ]
        }}
        """
    ).strip()


def build_long_content_generation_prompt(topic: TopicChoice, excluded_titles: list[str]) -> str:
    excluded = ", ".join(excluded_titles[-20:]) if excluded_titles else "None"
    return dedent(
        f"""
        Write a long-form YouTube video package in the same fast, curious, visual fact-explainer style as the Shorts,
        but expanded into a naturally paced 8:30 to 11:00 minute video.

        Topic:
        - Bucket: {topic.bucket}
        - Topic: {topic.topic}

        Constraints:
        - English only.
        - No emoji, no bullet labels inside narration, no stage directions.
        - Keep the tone conversational, curious, and clean.
        - Open with a strong hook, then build through clear segments with visual variety.
        - Include exactly 7 sections.
        - Each section narration should be 190-225 words.
        - Total narration should be 1325-1650 words.
        - Mention {topic.topic} early.
        - Use controlled clickbait: the title should create curiosity without lying or overpromising.
        - Use full ALL CAPS for some titles, and use ALL CAPS emphasis words in others; do not make every title all caps.
        - Avoid bland title shapes like "X Explained" or "A Visual Guide to X".
        - thumbnail_text must be short, punchy, ALL CAPS, mobile-readable, and clickbait-curious without lying.
        - Make thumbnail_text visually different from the title; use 2-4 big words, not a sentence.
        - Good thumbnail_text examples: "WAIT WHAT?", "HIDDEN TRUTH", "THIS IS WRONG", "NOBODY SEES THIS", "THEY HID THIS".
        - Every section needs 2-5 Pexels-friendly visual search queries.
        - Generate 8-20 tags without # symbols.
        - Facts must be complete sentences and distinct.
        - Avoid title similarity to these recent titles: {excluded}
        - Return strict JSON only.

        JSON schema:
        {{
          "bucket": "{topic.bucket}",
          "topic": "{topic.topic}",
          "title": "<clickable SEO-friendly long-form title>",
          "thumbnail_text": "<2-5 word ALL CAPS thumbnail phrase>",
          "description": "<2-4 paragraph YouTube description>",
          "tags": ["tag 1", "tag 2", "tag 3", "tag 4", "tag 5", "tag 6", "tag 7", "tag 8"],
          "sections": [
            {{
              "title": "<chapter title>",
              "narration": "<190-225 spoken words>",
              "visual_queries": ["<query 1>", "<query 2>"]
            }}
          ],
          "facts": ["<fact sentence 1>", "<fact sentence 2>", "<fact sentence 3>", "<fact sentence 4>", "<fact sentence 5>", "<fact sentence 6>"]
        }}
        """
    ).strip()
