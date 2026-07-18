from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import httpx

from youtube_kanaal.config import Settings
from youtube_kanaal.exceptions import PipelineStageError
from youtube_kanaal.models import GeneratedShort, TopicChoice
from youtube_kanaal.utils.files import write_json
from youtube_kanaal.utils.similarity import is_near_duplicate


@dataclass(frozen=True)
class WorldCupTeam:
    name: str
    score: int
    winner: bool
    possession: str | None
    shots_on_target: str | None


@dataclass(frozen=True)
class WorldCupEvent:
    event_id: str
    kickoff: datetime
    state: str
    completed: bool
    detail: str
    stage: str
    venue: str | None
    city: str | None
    country: str | None
    attendance: int | None
    home: WorldCupTeam
    away: WorldCupTeam
    source_url: str

    @property
    def topic(self) -> str:
        return f"{self.home.name} vs {self.away.name}"


class WorldCupService:
    """Fetches current 2026 World Cup scoreboards and builds fully grounded Shorts."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.network_timeout_seconds)

    def fetch_relevant_event(
        self,
        *,
        excluded_topics: list[str],
        excluded_titles: list[str] | None = None,
        response_path: Path,
        now: datetime | None = None,
    ) -> WorldCupEvent:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        raw_responses: list[dict[str, object]] = []
        events: list[WorldCupEvent] = []

        try:
            for offset in range(-self.settings.world_cup_past_days, self.settings.world_cup_future_days + 1):
                target_date = current.date() + timedelta(days=offset)
                date_value = target_date.strftime("%Y%m%d")
                response = self.client.get(self.settings.world_cup_scoreboard_url, params={"dates": date_value})
                response.raise_for_status()
                payload = response.json()
                raw_responses.append({"date": date_value, "payload": payload})
                events.extend(self._parse_events(payload))
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            write_json(response_path, {"responses": raw_responses, "error": str(exc)})
            raise PipelineStageError(
                stage="world_cup_data",
                message="Could not fetch trustworthy current World Cup data.",
                probable_cause=str(exc),
                details_path=response_path,
            ) from exc

        write_json(response_path, {"fetched_at": current.isoformat(), "responses": raw_responses})
        excluded = {topic.lower() for topic in excluded_topics}
        ranked_events = self._rank_events(events)
        candidates = [event for event in ranked_events if event.topic.lower() not in excluded]
        if candidates:
            return self._select_event_with_fresh_title(
                candidates,
                excluded_titles=excluded_titles or [],
            )

        reused_candidates = self._rank_reused_events(ranked_events, excluded_topics)
        if reused_candidates:
            return self._select_event_with_fresh_title(
                reused_candidates,
                excluded_titles=excluded_titles or [],
            )

        if not ranked_events:
            raise PipelineStageError(
                stage="world_cup_data",
                message="No 2026 World Cup matches were available in the configured date window.",
                probable_cause="Increase WORLD_CUP_PAST_DAYS/WORLD_CUP_FUTURE_DAYS.",
                details_path=response_path,
            )
        return ranked_events[0]

    def _rank_events(self, events: list[WorldCupEvent]) -> list[WorldCupEvent]:
        completed = sorted(
            (event for event in events if event.completed),
            key=lambda event: event.kickoff,
            reverse=True,
        )
        live = sorted(
            (event for event in events if event.state == "in"),
            key=lambda event: event.kickoff,
            reverse=True,
        )
        upcoming = sorted(
            (event for event in events if event.state == "pre"),
            key=lambda event: event.kickoff,
        )
        ranked = [*completed, *live, *upcoming]
        if not ranked and events:
            raise PipelineStageError(
                stage="world_cup_data",
                message="The World Cup source returned no usable completed, live, or upcoming matches.",
            )
        return ranked

    def _rank_reused_events(
        self,
        ranked_events: list[WorldCupEvent],
        excluded_topics: list[str],
    ) -> list[WorldCupEvent]:
        topic_age: dict[str, int] = {}
        for index, topic in enumerate(excluded_topics):
            topic_age.setdefault(topic.lower(), index)
        return sorted(
            ranked_events,
            key=lambda event: topic_age.get(event.topic.lower(), -1),
            reverse=True,
        )

    def _select_event_with_fresh_title(
        self,
        candidates: list[WorldCupEvent],
        *,
        excluded_titles: list[str],
    ) -> WorldCupEvent:
        if not excluded_titles:
            return candidates[0]
        for event in candidates:
            content = self.build_short(event, excluded_titles=excluded_titles)
            if not is_near_duplicate(content.title, excluded_titles, self.settings.similarity_threshold):
                return event
        return candidates[0]

    def topic_choice(self, event: WorldCupEvent) -> TopicChoice:
        return TopicChoice(
            bucket="world cup 2026",
            topic=event.topic,
            visual_queries=[
                f"{event.home.name} football fans",
                f"{event.away.name} football fans",
                "international soccer stadium",
                "football match crowd",
            ],
            search_terms=[event.topic, event.home.name, event.away.name, "2026 World Cup"],
        )

    def build_short(self, event: WorldCupEvent, *, excluded_titles: list[str] | None = None) -> GeneratedShort:
        if event.completed:
            return self._build_completed_short(event, excluded_titles=excluded_titles or [])
        if event.state == "in":
            return self._build_live_short(event)
        return self._build_upcoming_short(event, excluded_titles=excluded_titles or [])

    def _build_completed_short(self, event: WorldCupEvent, *, excluded_titles: list[str]) -> GeneratedShort:
        match_date = self._display_date(event.kickoff)
        total_goals = event.home.score + event.away.score
        margin = abs(event.home.score - event.away.score)
        if event.home.score == event.away.score:
            score = f"{event.home.score}-{event.away.score}"
            result_text = f"{event.home.name} and {event.away.name} drew {score}"
            result_fact = f"On {match_date}, {result_text} in {event.stage}."
            preferred_angle = "goal_draw" if total_goals >= 4 else self._completed_fallback_angle(event)
        else:
            winner = event.home if event.home.score > event.away.score else event.away
            loser = event.away if winner is event.home else event.home
            winner_score = f"{winner.score}-{loser.score}"
            result_text = f"{winner.name} beat {loser.name} {winner_score}"
            result_fact = f"On {match_date}, {result_text} in {event.stage}."
            preferred_angle = self._winning_angle(event, winner=winner, margin=margin)

        stat_fact = self._stat_fact(event)
        location_fact = self._location_fact(event)
        facts = [result_fact, stat_fact, location_fact]
        packages = []
        for angle in self._completed_angle_options(event, preferred_angle=preferred_angle, margin=margin, total_goals=total_goals):
            title, narration = self._completed_story(
                event,
                angle=angle,
                result_text=result_text,
                total_goals=total_goals,
                margin=margin,
                facts=facts,
            )
            packages.append(self._package(event, title=title, narration=narration, facts=facts, status_label="result"))
        return self._first_fresh_package(packages, excluded_titles)

    def _build_live_short(self, event: WorldCupEvent) -> GeneratedShort:
        score = f"{event.home.score}-{event.away.score}"
        total_goals = event.home.score + event.away.score
        facts = [
            f"{event.home.name} and {event.away.name} are currently at {score}.",
            f"The source lists the match status as {event.detail}.",
            self._location_fact(event),
        ]
        if total_goals >= 3:
            title = f"{event.topic} IS TURNING INTO A GOAL FEST"
            narration = (
                f"Stop scrolling, because {event.topic} is turning into a World Cup goal fest. "
                f"{facts[0]} That is already {total_goals} goals, and {facts[1].lower()} "
                f"{facts[2]} This is a live scoreboard snapshot, so the story can still change."
            )
        else:
            title = f"{event.topic}: THIS ONE IS STILL WIDE OPEN"
            narration = (
                f"{event.topic} is still wide open at the 2026 World Cup. "
                f"{facts[0]} {facts[1]} {facts[2]} "
                "Nothing beyond those confirmed live numbers is being predicted, and the score can still change."
            )
        return self._package(
            event,
            title=title,
            narration=narration,
            facts=facts,
            status_label="live update",
        )

    def _build_upcoming_short(self, event: WorldCupEvent, *, excluded_titles: list[str]) -> GeneratedShort:
        kickoff = f"{self._display_date(event.kickoff, include_year=False)} at {event.kickoff:%H:%M} UTC"
        facts = [
            f"{event.home.name} will face {event.away.name} on {kickoff}.",
            f"The match is listed as {event.stage}.",
            self._location_fact(event),
        ]
        stories = [
            (
                f"{event.topic}: THE NEXT WORLD CUP MATCH TO WATCH",
                (
                    f"Circle this one: {event.topic} is one of the next confirmed World Cup games. "
                    f"{facts[0]} {facts[1]} {facts[2]} "
                    "No fake prediction is needed here; the setting alone makes this a match worth watching."
                ),
            ),
            (
                f"DO NOT MISS {event.topic}",
                (
                    f"Do not miss {event.topic}, because the World Cup schedule has locked it in. "
                    f"{facts[0]} {facts[2]} {facts[1]} "
                    "Those are the verified details before kickoff, with no invented lineup news or score prediction."
                ),
            ),
            (
                f"{event.topic} HAS A DATE WITH THE WORLD CUP",
                (
                    f"{event.topic} now has an official World Cup date and location. "
                    f"{facts[1]} {facts[0]} {facts[2]} "
                    "That is everything confirmed before kickoff, and it is enough to put this game on the watchlist."
                ),
            ),
            (
                f"{event.home.name} vs {event.away.name}: SEMIFINAL DETAILS",
                (
                    f"{event.topic} is not a rumor; it is on the World Cup board. "
                    f"{facts[1]} {facts[0]} {facts[2]} "
                    "Before the first whistle, those confirmed details are the story. "
                    "The reason this matters is simple: once the semifinal stage arrives, every confirmed detail sets up the pressure."
                ),
            ),
            (
                f"WHERE {event.topic} WILL HAPPEN",
                (
                    f"The location matters for {event.topic}. "
                    f"{facts[2]} {facts[0]} {facts[1]} "
                    "This is a verified schedule snapshot, not a prediction. "
                    "That makes the match easy to track now, before any lineup rumor or score guess starts taking over."
                ),
            ),
        ]
        start = self._variation_index(event, len(stories))
        ordered_stories = [*stories[start:], *stories[:start]]
        packages = [
            self._package(event, title=title, narration=narration, facts=facts, status_label="match preview")
            for title, narration in ordered_stories
        ]
        return self._first_fresh_package(packages, excluded_titles)

    def _winning_angle(self, event: WorldCupEvent, *, winner: WorldCupTeam, margin: int) -> str:
        if margin >= 4:
            return "blowout"
        winner_possession = self._possession_value(winner.possession)
        opponent = event.away if winner is event.home else event.home
        opponent_possession = self._possession_value(opponent.possession)
        if winner_possession is not None and opponent_possession is not None and winner_possession < opponent_possession:
            return "less_ball"
        return self._completed_fallback_angle(event)

    def _completed_fallback_angle(self, event: WorldCupEvent) -> str:
        if event.attendance and event.attendance >= 65000:
            return "crowd"
        return ["numbers", "stadium", "result"][self._variation_index(event, 3)]

    def _completed_angle_options(
        self,
        event: WorldCupEvent,
        *,
        preferred_angle: str,
        margin: int,
        total_goals: int,
    ) -> list[str]:
        options = [preferred_angle]
        if margin >= 4:
            options.append("blowout")
        if total_goals >= 4 and event.home.score == event.away.score:
            options.append("goal_draw")
        if event.attendance:
            options.append("crowd")
        options.extend(["numbers", "stadium", "result"])
        return list(dict.fromkeys(options))

    def _completed_story(
        self,
        event: WorldCupEvent,
        *,
        angle: str,
        result_text: str,
        total_goals: int,
        margin: int,
        facts: list[str],
    ) -> tuple[str, str]:
        result_fact, stat_fact, location_fact = facts
        winner = event.home if event.home.score > event.away.score else event.away
        variant = self._variation_index(event, 2)
        possession_twist = (
            f"{winner.name} did it despite having less of the ball. "
            if angle == "less_ball"
            else ""
        )
        blowout_stories = [
            (
                f"THIS WORLD CUP SCORELINE IS BRUTAL: {result_text}",
                (
                    f"{event.topic} produced a scoreline that looks almost unreal. "
                    f"{result_fact} The winning margin was {margin} goals, with {total_goals} scored in total. "
                    f"{stat_fact} {location_fact} That is not just a win; that is a World Cup statement."
                ),
            ),
            (
                f"{winner.name} JUST SENT A WORLD CUP WARNING",
                (
                    f"This was not a normal win for {winner.name}; it was a warning. "
                    f"{result_fact} The gap was {margin} goals and the match produced {total_goals} in total. "
                    f"{location_fact} {stat_fact} The final score is the kind teams notice before their next game."
                ),
            ),
        ]
        less_ball_stories = [
            (
                f"{winner.name} WON WITHOUT CONTROLLING THE BALL",
                (
                    f"The strangest number from {event.topic} is not the final score. "
                    f"{result_fact} {possession_twist}{stat_fact} {location_fact} "
                    "The scoreboard is a reminder that possession can look convincing and still fail to decide the result."
                ),
            ),
            (
                f"POSSESSION LIED IN {event.topic}",
                (
                    f"Possession told one story in {event.topic}, but the scoreboard told another. "
                    f"{stat_fact} Even so, {result_text} in {event.stage}. "
                    f"{location_fact} {result_fact} Having more of the ball meant nothing when the final score arrived."
                ),
            ),
        ]
        stories = {
            "blowout": blowout_stories[variant],
            "less_ball": less_ball_stories[variant],
            "goal_draw": (
                f"THIS WORLD CUP DRAW HAD {total_goals} GOALS",
                (
                    f"{event.topic} refused to produce a winner, but it definitely produced drama. "
                    f"{result_fact} The teams combined for {total_goals} goals without separating themselves. "
                    f"{stat_fact} {location_fact} A draw can still feel completely chaotic."
                ),
            ),
            "crowd": (
                f"OVER {event.attendance:,} WATCHED {event.topic}",
                (
                    f"The crowd number behind {event.topic} is massive. "
                    f"{location_fact} And they watched {result_text} in {event.stage}. "
                    f"{stat_fact} {result_fact} That is a lot of people packed into one World Cup memory."
                ),
            ),
            "stadium": (
                f"{event.topic}: THE STADIUM SAW EVERYTHING",
                (
                    f"{event.topic} had a setting built for a World Cup moment. "
                    f"{location_fact} {result_fact} {stat_fact} "
                    f"The confirmed numbers say {total_goals} total goals, but the location gives the result its scale."
                ),
            ),
            "result": (
                f"{event.topic}: THE RESULT NOBODY CAN IGNORE",
                (
                    f"{event.topic} just left a result that matters. "
                    f"{result_fact} {location_fact} {stat_fact} "
                    f"The final score contained {total_goals} total goals, and every one of those numbers is now part of the tournament."
                ),
            ),
            "numbers": (
                f"THE NUMBERS BEHIND {event.topic}",
                (
                    f"One number does not tell the whole story of {event.topic}. "
                    f"{result_fact} {stat_fact} {location_fact} "
                    f"Put together, the score, possession and crowd make this much more interesting than a basic result graphic."
                ),
            ),
        }
        return stories[angle]

    def _variation_index(self, event: WorldCupEvent, size: int) -> int:
        digest = hashlib.sha256(f"{event.event_id}|{event.topic}".encode("utf-8")).digest()
        return int.from_bytes(digest[:4], byteorder="big") % size

    def _first_fresh_package(
        self,
        packages: list[GeneratedShort],
        excluded_titles: list[str],
    ) -> GeneratedShort:
        if not packages:
            raise PipelineStageError(
                stage="world_cup_data",
                message="The World Cup event could not be converted into usable Short content.",
            )
        for package in packages:
            if not is_near_duplicate(package.title, excluded_titles, self.settings.similarity_threshold):
                return package
        return packages[0]

    def _possession_value(self, value: str | None) -> float | None:
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    def _package(
        self,
        event: WorldCupEvent,
        *,
        title: str,
        narration: str,
        facts: list[str],
        status_label: str,
    ) -> GeneratedShort:
        trimmed_title = title[:70].rstrip(" -:,")
        return GeneratedShort(
            bucket="world cup 2026",
            topic=event.topic,
            title=trimmed_title,
            title_hook=trimmed_title,
            description=(
                f"A verified 2026 FIFA World Cup {status_label} for {event.topic}. "
                f"Scoreboard source: {event.source_url}"
            ),
            hashtags=[
                "#WorldCup",
                "#FIFAWorldCup",
                "#WorldCup2026",
                "#Football",
                "#Soccer",
                "#FootballShorts",
                "#SportsNews",
                "#MatchUpdate",
                f"#{event.home.name.replace(' ', '')}",
                f"#{event.away.name.replace(' ', '')}",
            ],
            narration=narration,
            facts=facts,
            subtitle_text=narration,
        )

    def _display_date(self, value: datetime, *, include_year: bool = True) -> str:
        rendered = f"{value:%B} {value.day}"
        return f"{rendered}, {value.year}" if include_year else rendered

    def _stat_fact(self, event: WorldCupEvent) -> str:
        if event.home.possession and event.away.possession:
            return (
                f"Possession was {event.home.possession} percent for {event.home.name} "
                f"and {event.away.possession} percent for {event.away.name}."
            )
        if event.home.shots_on_target and event.away.shots_on_target:
            return (
                f"{event.home.name} had {event.home.shots_on_target} shots on target, "
                f"while {event.away.name} had {event.away.shots_on_target}."
            )
        return f"The official scoreboard marked the match as {event.detail}."

    def _location_fact(self, event: WorldCupEvent) -> str:
        location = ", ".join(part for part in [event.venue, event.city, event.country] if part)
        if location and event.attendance:
            return f"The match was played at {location} in front of {event.attendance:,} spectators."
        if location:
            verb = "was played" if event.completed else "is scheduled"
            return f"The match {verb} at {location}."
        return f"The scoreboard identifies this game as {event.stage}."

    def _parse_events(self, payload: object) -> list[WorldCupEvent]:
        if not isinstance(payload, dict):
            return []
        parsed: list[WorldCupEvent] = []
        for raw_event in payload.get("events", []):
            if not isinstance(raw_event, dict):
                continue
            competitions = raw_event.get("competitions")
            if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
                continue
            competition = competitions[0]
            competitors = competition.get("competitors")
            if not isinstance(competitors, list):
                continue
            home_raw = next((item for item in competitors if item.get("homeAway") == "home"), None)
            away_raw = next((item for item in competitors if item.get("homeAway") == "away"), None)
            if not isinstance(home_raw, dict) or not isinstance(away_raw, dict):
                continue
            status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
            status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
            venue = competition.get("venue") if isinstance(competition.get("venue"), dict) else {}
            address = venue.get("address") if isinstance(venue.get("address"), dict) else {}
            try:
                kickoff = datetime.fromisoformat(str(raw_event["date"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            parsed.append(
                WorldCupEvent(
                    event_id=str(raw_event.get("id", "")),
                    kickoff=kickoff,
                    state=str(status_type.get("state", "")),
                    completed=bool(status_type.get("completed", False)),
                    detail=str(status_type.get("detail") or status_type.get("description") or "scheduled"),
                    stage=str(competition.get("altGameNote") or "2026 FIFA World Cup"),
                    venue=str(venue.get("fullName")) if venue.get("fullName") else None,
                    city=str(address.get("city")) if address.get("city") else None,
                    country=str(address.get("country")) if address.get("country") else None,
                    attendance=int(competition["attendance"]) if competition.get("attendance") else None,
                    home=self._parse_team(home_raw),
                    away=self._parse_team(away_raw),
                    source_url=f"https://www.espn.com/soccer/match/_/gameId/{raw_event.get('id', '')}",
                )
            )
        return parsed

    def _parse_team(self, raw: dict[str, object]) -> WorldCupTeam:
        team = raw.get("team") if isinstance(raw.get("team"), dict) else {}
        statistics = raw.get("statistics") if isinstance(raw.get("statistics"), list) else []
        stat_values = {
            str(stat.get("name")): str(stat.get("displayValue"))
            for stat in statistics
            if isinstance(stat, dict) and stat.get("name") and stat.get("displayValue") is not None
        }
        return WorldCupTeam(
            name=str(team.get("displayName") or team.get("name") or "Unknown team"),
            score=int(raw.get("score") or 0),
            winner=bool(raw.get("winner", False)),
            possession=stat_values.get("possessionPct"),
            shots_on_target=stat_values.get("shotsOnTarget"),
        )
