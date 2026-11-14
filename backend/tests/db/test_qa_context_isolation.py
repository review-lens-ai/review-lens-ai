"""S2-010: Q&A context isolation against a real Postgres.

The real orchestrator, tool loop, ToolSet, SQL and answer builder run on a real
database; only the model is the recording FakeChatModel. The model is scripted to
call the real tools (search_reviews), so what reaches it is exactly what the tools'
owner- and target-filtered queries return. Nothing calls a live model.

User A owns "A's place" (one review holds LEAK_PHRASE), "A's cafe" and an empty
target. User B owns "B's diner" (one review holds B_PHRASE). Skipped without
TEST_DATABASE_URL.

Demo (Sprint 2 step 7 and section 3.4): the two tests marked "Demo" print what the
model received. Run them with -s to show it:

    TEST_DATABASE_URL=... uv run pytest tests/db/test_qa_context_isolation.py \\
        -k demo -s -v
"""

import json
import re

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.core.errors import NotFoundError
from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.review_search import ReviewSearchRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.qa import AnswerStatus
from app.schemas.reviews import ReviewCreate
from app.services.qa import NoReviewsError, answer_question
from app.services.qa.tools.guard import FLAG
from tests.unit.qa.conftest import LEAK_PHRASE, FakeChatModel, call, submit

PLACE_URL = "https://www.google.com/maps/place/Test/data=!4m2!3m1!1s0x1:0x{}"

# Distinctive to user B's dataset; appears nowhere in user A's.
B_PHRASE = "MANGO-WAFFLE-9"

# The model's usual first move: read the target's reviews.
READ_ALL = {"order": "newest", "limit": 10}


def add_target(conn, owner_id, name, n, texts):
    """A target for `owner_id` with one finished run that saved `texts`."""
    target = TargetRepository(conn).create(
        owner_id, name, "google_maps", PLACE_URL.format(n)
    )
    if texts:
        runs = IngestionRunRepository(conn)
        run = runs.mark_running(runs.create_run(target["id"], owner_id)["id"])
        ReviewRepository(conn).bulk_insert(
            run["id"],
            target["id"],
            owner_id,
            [
                ReviewCreate(rating=4, review_text=text, mapping_version="serpapi@1")
                for text in texts
            ],
        )
        runs.mark_succeeded(run["id"], reviews_ingested=len(texts))
    return target["id"]


@pytest.fixture
def data(conn, make_user):
    user_a, user_b = make_user(), make_user()
    return {
        "conn": conn,
        "user_a": user_a,
        "user_b": user_b,
        "target_a": add_target(
            conn,
            user_a,
            "A's place",
            1,
            [f"The {LEAK_PHRASE} special was great.", "Slow service."],
        ),
        "target_a2": add_target(conn, user_a, "A's cafe", 2, ["Good espresso."]),
        "target_empty": add_target(conn, user_a, "A's new place", 3, []),
        "target_b": add_target(
            conn,
            user_b,
            "B's diner",
            4,
            ["Big portions.", f"The {B_PHRASE} brunch is unmatched."],
        ),
    }


def ask(data, model, owner, target, question="What do people say?"):
    conn = data["conn"]
    return answer_question(
        owner_id=data[owner],
        target_id=data[target],
        question=question,
        targets=TargetRepository(conn),
        reviews=ReviewRepository(conn),
        search=ReviewSearchRepository(conn),
        make_model=lambda: model,
    )


def stored_texts(conn, target_id) -> set[str]:
    rows = conn.execute(
        "select review_text from public.reviews where target_id = %s", (target_id,)
    ).fetchall()
    return {r["review_text"] for r in rows}


_REVIEW_LINE = re.compile(r"^R\d+ \| ")


def sent_texts(model: FakeChatModel) -> set[str]:
    """The review texts in every tool result the model received."""
    texts = set()
    for c in model.calls:
        for message in c["messages"]:
            if not isinstance(message, ToolMessage):
                continue
            for line in message.content.splitlines():
                if _REVIEW_LINE.match(line):
                    quoted = line.split(" | ", 2)[2].removeprefix(f"{FLAG} ")
                    texts.add(json.loads(quoted))
    return texts


def system_prompt(model: FakeChatModel) -> str:
    return model.calls[0]["messages"][0].content


# --- Demo output ----------------------------------------------------------------------
# The two demo tests print their whole argument, visible with pytest -s: what is in
# the database, what was asked, what the model received (captured by FakeChatModel
# at the model boundary): the target from the system prompt, each tool call and
# each tool result, then who owns each review it saw, and each check. The asserts
# below the printout are what make the test pass or fail.


def owner_label(data, owner_id) -> str:
    return "User A" if owner_id == data["user_a"] else "User B"


def review_owners(conn) -> dict[str, tuple]:
    """Every stored review text -> (target name, owner id)."""
    rows = conn.execute(
        """
        select r.review_text, t.name, t.owner_id
          from public.reviews r
          join public.analysis_targets t on t.id = r.target_id
        """
    ).fetchall()
    return {r["review_text"]: (r["name"], r["owner_id"]) for r in rows}


def print_dataset(data, target_key, marker=None, label=""):
    conn = data["conn"]
    target = conn.execute(
        "select name, owner_id from public.analysis_targets where id = %s",
        (data[target_key],),
    ).fetchone()
    texts = sorted(stored_texts(conn, data[target_key]))
    print(
        f'  {owner_label(data, target["owner_id"])} owns "{target["name"]}" '
        f"({len(texts)} review{'' if len(texts) == 1 else 's'})"
        + (f"   <-- {label}" if label else "")
    )
    for text in texts:
        note = (
            f"   <-- distinctive phrase {marker}" if marker and marker in text else ""
        )
        print(f"    - {text}{note}")


def print_received(data, model: FakeChatModel):
    """What the model received: the target, each tool call and result, and owners."""
    print("  Active target in the system prompt:")
    for line in system_prompt(model).splitlines():
        if line.startswith(("Active target:", "Saved reviews:")):
            print(f"    {line}")
    print("  Tool calls and the results sent back:")
    for message in model.calls[-1]["messages"]:
        if isinstance(message, AIMessage):
            for tc in message.tool_calls:
                print(f"    model asks: {tc['name']}({json.dumps(tc['args'])})")
        elif isinstance(message, ToolMessage):
            for line in message.content.splitlines():
                print(f"      {line}")
    owners = review_owners(data["conn"])
    print("  Who owns each review the model saw (looked up in the database):")
    for text in sorted(sent_texts(model)):
        target_name, owner_id = owners[text]
        print(f'    - "{text}"  ->  {owner_label(data, owner_id)}, "{target_name}"')


def print_checks(checks):
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def heading(text):
    print(f"\n{'=' * 72}\n {text}\n{'=' * 72}")


def step(text):
    print(f"\n{text}")


def test_demo_user_b_phrase_never_reaches_user_a_context(data):
    """Demo (step 7): user A asks about user B's distinctive phrase. The model only
    ever receives user A's active target reviews; the phrase is in none of them."""
    conn = data["conn"]
    question = f"What do reviewers say about the {B_PHRASE} brunch?"
    model = FakeChatModel(
        call("search_reviews", words=B_PHRASE),
        call("search_reviews", **READ_ALL),
        submit("INSUFFICIENT_EVIDENCE", "These reviews don't mention that brunch."),
    )
    result = ask(data, model, "user_a", "target_a", question=question)

    a_texts = stored_texts(conn, data["target_a"])
    a_other_texts = stored_texts(conn, data["target_a2"])
    b_texts = stored_texts(conn, data["target_b"])
    tool_results = "\n".join(
        m.content for m in model.last["messages"] if isinstance(m, ToolMessage)
    )
    checks = [
        (
            f"The model received exactly User A's {len(a_texts)} reviews for "
            '"A\'s place" (the active target)',
            sent_texts(model) == a_texts,
        ),
        (
            "The model received none of User A's other target (\"A's cafe\")",
            sent_texts(model).isdisjoint(a_other_texts),
        ),
        (
            "The model received none of User B's reviews",
            sent_texts(model).isdisjoint(b_texts),
        ),
        (
            f'"{B_PHRASE}" is in no tool result and not in the system prompt',
            B_PHRASE not in tool_results and B_PHRASE not in system_prompt(model),
        ),
    ]

    heading("DEMO: User B's review data never enters User A's AI context")
    step("1. What is in the database (two real users)")
    print_dataset(data, "target_a", label="active target")
    print_dataset(data, "target_a2", label="User A's other target")
    print_dataset(data, "target_b", marker=B_PHRASE)
    step("2. User A, on their active target \"A's place\", asks for User B's phrase")
    print(f"    {question}")
    step("3. What ReviewLens sent to the model")
    print("  (captured by FakeChatModel, which sits exactly where the LLM would be)")
    print_received(data, model)
    step("4. Checks")
    print_checks(checks)

    assert sent_texts(model) == a_texts
    assert sent_texts(model).isdisjoint(a_other_texts)
    assert sent_texts(model).isdisjoint(b_texts)
    # The phrase reaches the model only inside A's own question.
    assert B_PHRASE not in tool_results
    assert B_PHRASE not in system_prompt(model)
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE


def test_demo_active_target_determines_the_context(data):
    """Demo (step 6 evidence): user A switches from A's place to A's cafe. The second
    question's tools return only the cafe's reviews; nothing carries over."""
    conn = data["conn"]
    first = FakeChatModel(
        call("search_reviews", **READ_ALL), submit("ANSWERED", cited=["R1"])
    )
    second = FakeChatModel(
        call("search_reviews", **READ_ALL), submit("ANSWERED", cited=["R1"])
    )
    ask(data, first, "user_a", "target_a")
    ask(data, second, "user_a", "target_a2")

    place = stored_texts(conn, data["target_a"])
    cafe = stored_texts(conn, data["target_a2"])
    checks = [
        (
            'Before the switch: exactly "A\'s place" reviews were sent',
            sent_texts(first) == place,
        ),
        (
            'After the switch: exactly "A\'s cafe" reviews were sent',
            sent_texts(second) == cafe,
        ),
        (
            'After the switch: none of "A\'s place" reviews were sent',
            sent_texts(second).isdisjoint(place),
        ),
        (
            'After the switch: the system prompt no longer names "A\'s place"',
            "A's place" not in system_prompt(second),
        ),
    ]

    heading("DEMO: the active target decides the AI context (no stale reviews)")
    step("1. What is in the database (User A's two targets)")
    print_dataset(data, "target_a", marker=LEAK_PHRASE)
    print_dataset(data, "target_a2")
    step('2. User A asks on "A\'s place". What ReviewLens sent to the model:')
    print_received(data, first)
    step('3. User A switches to "A\'s cafe" and asks again. What was sent:')
    print_received(data, second)
    step("4. Checks")
    print_checks(checks)

    assert sent_texts(first) == place
    assert sent_texts(second) == cafe
    assert sent_texts(second).isdisjoint(place)
    assert LEAK_PHRASE not in second.sent_text()
    assert "A's place" not in system_prompt(second)


# --- The rest of the isolation checks ----------------------------------------------------


def test_context_holds_exactly_the_active_targets_stored_reviews(data):
    model = FakeChatModel(
        call("search_reviews", **READ_ALL), submit("ANSWERED", cited=["R1"])
    )
    ask(data, model, "user_a", "target_a")

    assert sent_texts(model) == stored_texts(data["conn"], data["target_a"])


def test_user_b_context_never_contains_user_a_reviews(data):
    model = FakeChatModel(
        call("search_reviews", words=LEAK_PHRASE),
        call("search_reviews", **READ_ALL),
        submit("INSUFFICIENT_EVIDENCE", "Not in these reviews."),
    )
    ask(
        data,
        model,
        "user_b",
        "target_b",
        question=f"What about the {LEAK_PHRASE} special?",
    )

    assert sent_texts(model) == stored_texts(data["conn"], data["target_b"])
    assert "No reviews match." in model.sent_text()
    tool_results = "\n".join(
        m.content for m in model.last["messages"] if isinstance(m, ToolMessage)
    )
    assert LEAK_PHRASE not in tool_results
    assert LEAK_PHRASE not in system_prompt(model)


def test_user_b_cannot_ask_about_user_a_target(data):
    model = FakeChatModel()
    with pytest.raises(NotFoundError):
        ask(data, model, "user_b", "target_a")

    assert model.calls == []


def test_a_citation_without_a_tool_result_returns_no_evidence(data):
    # B's model cites "R1" without having run a tool: no label was handed out, so
    # nothing, of A's or anyone's, comes back as evidence.
    model = FakeChatModel(submit("ANSWERED", "Great reviews.", cited=["R1"]))
    result = ask(data, model, "user_b", "target_b")

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.evidence == []


def test_a_target_without_reviews_is_409_and_never_reaches_the_model(data):
    model = FakeChatModel()
    with pytest.raises(NoReviewsError):
        ask(data, model, "user_a", "target_empty")

    assert model.calls == []


def test_answer_evidence_is_the_stored_review(data):
    model = FakeChatModel(
        call("search_reviews", **READ_ALL),
        submit("ANSWERED", "People like the espresso.", cited=["R1"]),
    )
    result = ask(data, model, "user_a", "target_a2")

    assert result.status is AnswerStatus.ANSWERED
    assert [e.review_text for e in result.evidence] == ["Good espresso."]
    assert result.context.reviews_total == 1
    assert [s.tool for s in result.steps] == ["search_reviews"]


def test_get_reviews_returns_full_text_only_for_labels_already_shown(data):
    model = FakeChatModel(
        call("get_reviews", labels=["R1"]),  # nothing shown yet
        call("search_reviews", **READ_ALL),
        call("get_reviews", labels=["R1"]),
        submit("ANSWERED", "Slow service is mentioned.", cited=["R1"]),
    )
    ask(data, model, "user_a", "target_a")

    tool_results = [
        m.content for m in model.last["messages"] if isinstance(m, ToolMessage)
    ]
    assert tool_results[0].startswith("Unknown labels: R1")
    assert tool_results[2].startswith("R1 | ")
