"""Responsive Streamlit chat client for the SQL Agent API."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from ui.api_client import AgentAPIClient, AgentAPIError

st.set_page_config(
    page_title="Clarity Assistant",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False

if st.session_state.dark_mode:
    theme_variables = """
    <style>
    :root {
        color-scheme: dark;
        --brand: #9b8cff;
        --brand-soft: #242039;
        --ink: #f5f3f8;
        --muted: #a29eaa;
        --line: #292731;
        --surface: #09090c;
        --surface-soft: #15141a;
        --sidebar: #101014;
        --header: rgba(9, 9, 12, 0.97);
        --user-bubble: #1d2944;
        --user-border: #304369;
        --user-text: #edf2ff;
        --shadow: rgba(0, 0, 0, 0.32);
        --hover-border: #7565d7;
        --hover-text: #d8d1ff;
    }
    </style>
    """
else:
    theme_variables = """
    <style>
    :root {
        color-scheme: light;
        --brand: #6552d9;
        --brand-soft: #f0edff;
        --ink: #17171c;
        --muted: #6e6d78;
        --line: #e8e7ec;
        --surface: #ffffff;
        --surface-soft: #f7f7f9;
        --sidebar: #fafafa;
        --header: rgba(255, 255, 255, 0.96);
        --user-bubble: #eef2ff;
        --user-border: #dbe4ff;
        --user-text: #1f2a44;
        --shadow: rgba(24, 20, 48, 0.08);
        --hover-border: #cfc7ff;
        --hover-text: #3f2daf;
    }
    </style>
    """

st.markdown(theme_variables, unsafe_allow_html=True)

st.markdown(
    """
    <style>
    .stApp {
        background: var(--surface);
        color: var(--ink);
        scroll-behavior: smooth;
    }

    [data-testid="stAppViewContainer"],
    [data-testid="stMain"] {
        background: var(--surface);
    }

    #latest-user-message {
        scroll-margin-top: 5rem;
    }

    header[data-testid="stHeader"] {
        background: var(--header);
        border-bottom: 1px solid var(--line);
    }

    [data-testid="stBottom"],
    [data-testid="stBottom"] > div,
    [data-testid="stBottomBlockContainer"] {
        background: var(--surface) !important;
    }

    [data-testid="stBottom"] {
        border-top: 1px solid var(--line);
        backdrop-filter: blur(10px);
    }

    [data-testid="stBottomBlockContainer"] {
        padding-top: 0.85rem;
        padding-bottom: 1rem;
    }

    .block-container {
        max-width: 920px;
        padding-top: 1.4rem;
        padding-bottom: 7rem;
    }

    [data-testid="stSidebar"] {
        border-right: 1px solid var(--line);
        background: var(--sidebar);
    }

    [data-testid="stSidebar"] .block-container {
        padding-top: 1.25rem;
    }

    .app-kicker {
        color: var(--brand);
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.11em;
        text-transform: uppercase;
        margin-bottom: 0.25rem;
    }

    .app-title {
        color: var(--ink);
        font-size: 1.12rem;
        font-weight: 680;
        letter-spacing: -0.01em;
        margin: 0;
    }

    .app-subtitle {
        color: var(--muted);
        font-size: 0.84rem;
        margin-top: 0.2rem;
    }

    .welcome {
        padding: 4.5rem 0 2.25rem;
        text-align: center;
    }

    .welcome-mark {
        align-items: center;
        background: var(--brand);
        border-radius: 14px;
        color: white;
        display: inline-flex;
        font-size: 1.35rem;
        height: 46px;
        justify-content: center;
        margin-bottom: 1rem;
        width: 46px;
    }

    .welcome h1 {
        font-size: clamp(1.75rem, 5vw, 2.35rem);
        letter-spacing: -0.035em;
        margin: 0 0 0.6rem;
    }

    .welcome p {
        color: var(--muted);
        line-height: 1.55;
        margin: 0 auto;
        max-width: 590px;
    }

    [data-testid="stChatMessage"] {
        background: transparent;
        gap: 0.7rem;
        margin-bottom: 1.2rem;
        padding: 0;
    }

    /* User prompts are compact, right aligned, and visually subordinate. */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        justify-content: flex-end;
    }

    [data-testid="stChatMessageAvatarUser"] {
        display: none;
    }

    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
    [data-testid="stChatMessageContent"] {
        background: var(--user-bubble);
        border: 1px solid var(--user-border);
        border-radius: 15px 15px 4px 15px;
        color: var(--user-text);
        flex: 0 1 auto;
        margin-left: auto;
        max-width: min(72%, 620px);
        padding: 0.72rem 0.95rem;
        width: fit-content;
    }

    /* Assistant replies stay flat and readable instead of mimicking SMS. */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
    [data-testid="stChatMessageContent"] {
        max-width: 72ch;
        width: 100%;
    }

    [data-testid="stChatMessageAvatarAssistant"] {
        background: var(--brand);
        border: 0;
        color: white;
    }

    [data-testid="stChatMessageContent"] p {
        line-height: 1.65;
    }

    [data-testid="stChatMessageContent"] p:first-child {
        margin-top: 0;
    }

    [data-testid="stChatMessageContent"] pre {
        border: 1px solid var(--line);
        border-radius: 12px;
    }

    [data-testid="stChatInput"] {
        background: var(--surface-soft) !important;
        border: 1px solid var(--line);
        border-radius: 16px;
        box-shadow: 0 10px 30px var(--shadow);
    }

    [data-testid="stChatInput"] > div,
    [data-testid="stChatInput"] [data-baseweb="textarea"],
    [data-testid="stChatInput"] textarea {
        background: transparent !important;
    }

    [data-testid="stChatInput"] textarea {
        color: var(--ink);
        caret-color: var(--brand);
    }

    [data-testid="stChatInput"] textarea::placeholder {
        color: var(--muted);
        opacity: 1;
    }

    [data-testid="stChatInput"]:focus-within {
        border-color: var(--brand);
        box-shadow: 0 0 0 2px rgba(101, 82, 217, 0.18);
    }

    [data-testid="stChatInputSubmitButton"] {
        background: var(--brand);
        border-radius: 10px;
        color: white;
    }

    /* Streamlit buttons otherwise inherit the viewer's dark-mode palette. */
    .stButton > button[kind="secondary"],
    button[data-testid="baseButton-secondary"] {
        background: var(--surface);
        border: 1px solid var(--line);
        color: var(--ink);
    }

    .stButton > button[kind="secondary"]:hover,
    button[data-testid="baseButton-secondary"]:hover {
        background: var(--brand-soft);
        border-color: var(--hover-border);
        color: var(--hover-text);
    }

    .stButton > button[kind="primary"],
    button[data-testid="baseButton-primary"] {
        background: var(--brand);
        border-color: var(--brand);
        color: white;
    }

    .stButton > button:focus-visible,
    [data-testid="stChatInput"]:focus-within {
        outline: 2px solid #3b82f6;
        outline-offset: 2px;
    }

    div[data-testid="stAlert"] {
        border-radius: 12px;
    }

    div[data-testid="stExpander"] {
        background: var(--surface-soft);
        border: 1px solid var(--line);
        border-radius: 10px;
    }

    .assistant-meta {
        color: var(--muted);
        font-size: 0.74rem;
        margin-bottom: 0.48rem;
    }

    .privacy-note {
        color: var(--muted);
        font-size: 0.72rem;
        line-height: 1.45;
    }

    .stApp,
    [data-testid="stSidebar"],
    [data-testid="stMarkdownContainer"],
    [data-testid="stWidgetLabel"] {
        color: var(--ink);
    }

    [data-testid="stCaptionContainer"] {
        color: var(--muted);
    }

    [data-testid="stCodeBlock"],
    [data-testid="stJson"] {
        background: var(--surface-soft);
        color: var(--ink);
    }

    @media (max-width: 760px) {
        .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
            padding-top: 0.9rem;
        }

        [data-testid="stSidebar"] {
            min-width: 250px;
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
        [data-testid="stChatMessageContent"] {
            max-width: 88%;
        }

        .welcome {
            padding-top: 2.5rem;
        }

    }

    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            scroll-behavior: auto !important;
            transition-duration: 0.01ms !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

api = AgentAPIClient()
MAX_VISIBLE_MESSAGES = 20

# The visible transcript is bounded and process-memory-only. It is display
# state, not authority: the API receives only the new prompt and opaque thread.
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "awaiting_clarification" not in st.session_state:
    st.session_state.awaiting_clarification = False
if "messages" not in st.session_state:
    st.session_state.messages = []


def clear_control_state() -> None:
    st.session_state.thread_id = None
    st.session_state.awaiting_clarification = False


def clear_local_conversation() -> None:
    clear_control_state()
    st.session_state.messages = []


def clear_conversation() -> None:
    """Clear both the API thread and this browser session's display state."""
    current_thread = st.session_state.thread_id
    if isinstance(current_thread, str) and current_thread:
        try:
            api.clear_thread(current_thread)
        except AgentAPIError as exc:
            st.warning(f"Local conversation cleared. Server cleanup failed: {exc}")
    clear_local_conversation()


def remember_message(message: dict) -> None:
    st.session_state.messages = [
        *st.session_state.messages,
        message,
    ][-MAX_VISIBLE_MESSAGES:]


def scroll_to_latest_user() -> None:
    """Smoothly place the newest prompt at the top of the conversation area."""
    components.html(
        """
        <script>
        const target = window.parent.document.getElementById("latest-user-message");
        if (target) {
            const reduceMotion = window.parent.matchMedia(
                "(prefers-reduced-motion: reduce)"
            ).matches;
            target.scrollIntoView({
                behavior: reduceMotion ? "auto" : "smooth",
                block: "start"
            });
        }
        </script>
        """,
        height=0,
    )


def render_response(response: dict) -> None:
    """Render a public API response inside an assistant message."""
    status = response.get("status")
    model_label = "Clarity Assistant · Claude"
    st.markdown(f'<div class="assistant-meta">{model_label}</div>', unsafe_allow_html=True)

    if status == "rejected":
        st.error(response.get("policy_reason") or "This request was blocked by policy.")
        return
    if status == "error":
        st.error(response.get("answer") or "The request could not be completed.")
        return

    if response.get("answer"):
        st.markdown(response["answer"])

    if response.get("generated_sql"):
        st.markdown("**Validated SQL draft**")
        st.code(response["generated_sql"], language="sql")

        parameters = response.get("query_parameters") or []
        if parameters:
            with st.expander("Named query parameters"):
                st.json(parameters)

        if response.get("execution_status") == "not_configured":
            st.info("This draft was validated, but database execution is not configured.")

    citations = response.get("citation_details") or []
    if citations:
        with st.expander(f"Sources ({len(citations)})"):
            for citation in citations:
                number = citation["reference_number"]
                st.markdown(f"**[{number}] {citation['label']}**")
                st.caption(
                    f"{citation['source_file']} · {citation.get('category') or 'documentation'}"
                )

    if response.get("interrupted"):
        st.warning(response.get("clarification_prompt") or "Please clarify your request.")


def apply_response_state(response: dict) -> None:
    """Update opaque workflow control state, separately from display history."""
    if response.get("status") == "rejected":
        clear_control_state()
    elif response.get("interrupted"):
        st.session_state.thread_id = response["thread_id"]
        st.session_state.awaiting_clarification = True
    else:
        st.session_state.thread_id = response.get("thread_id")
        st.session_state.awaiting_clarification = False


with st.sidebar:
    st.markdown('<div class="app-kicker">Northwell</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-title">Clarity Assistant</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="app-subtitle">Documentation and validated SQL drafts</div>',
        unsafe_allow_html=True,
    )
    st.write("")

    st.toggle("Dark mode", key="dark_mode", help="Switch the chat color theme")
    st.write("")

    if st.button("＋  New conversation", use_container_width=True, type="primary"):
        clear_conversation()
        st.rerun()

    st.markdown("#### Current conversation")
    if st.session_state.messages:
        first_prompt = next(
            (
                message.get("content", "")
                for message in st.session_state.messages
                if message.get("role") == "user"
            ),
            "Untitled conversation",
        )
        title = first_prompt if len(first_prompt) <= 34 else f"{first_prompt[:34]}…"
        st.markdown(f"▣ &nbsp; {title}")
        st.caption(f"{len(st.session_state.messages)} visible messages")
    else:
        st.caption("No messages yet")

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        clear_conversation()
        st.rerun()

    st.markdown(
        '<p class="privacy-note">Chat is bounded to this browser session. '
        "Clear removes the visible transcript and server thread context.</p>",
        unsafe_allow_html=True,
    )

if not st.session_state.messages:
    st.markdown(
        """
        <section class="welcome">
            <div class="welcome-mark">✦</div>
            <h1>What would you like to analyze?</h1>
            <p>Ask about approved Epic Clarity tables and columns, explore documentation,
            or request a bounded aggregate SQL draft.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.caption("Try an example")
    example_columns = st.columns(2)
    examples = (
        "What is the A0H_MAP table?",
        "Which columns describe account configuration?",
    )
    for column, example in zip(example_columns, examples, strict=True):
        with column:
            if st.button(example, use_container_width=True):
                st.session_state.pending_prompt = example
                st.rerun()

latest_user_index = next(
    (
        index
        for index in range(len(st.session_state.messages) - 1, -1, -1)
        if st.session_state.messages[index].get("role") == "user"
    ),
    None,
)

for index, message in enumerate(st.session_state.messages):
    role = message.get("role", "assistant")
    avatar = "👤" if role == "user" else "✨"
    if index == latest_user_index:
        st.markdown('<div id="latest-user-message"></div>', unsafe_allow_html=True)
    with st.chat_message(role, avatar=avatar):
        if role == "user":
            st.write(message.get("content", ""))
        else:
            render_response(message.get("response") or {})

if st.session_state.awaiting_clarification:
    st.info("The assistant needs one detail before it can continue.")

placeholder = (
    "Add the requested table, column, or topic…"
    if st.session_state.awaiting_clarification
    else "Ask about approved Clarity documentation…"
)
typed_prompt = st.chat_input(placeholder)
prompt = st.session_state.pop("pending_prompt", None) or typed_prompt

if prompt:
    remember_message({"role": "user", "content": prompt})

    # The history loop has already run for this Streamlit pass, so render the
    # new prompt now. This makes the sequence honest: user message, processing
    # state, then assistant response after the API call completes.
    st.markdown('<div id="latest-user-message"></div>', unsafe_allow_html=True)
    with st.chat_message("user", avatar="👤"):
        st.write(prompt)
    scroll_to_latest_user()

    try:
        with st.status("Checking policy and searching approved documentation…", expanded=False):
            if st.session_state.awaiting_clarification:
                thread_id = st.session_state.thread_id
                if not isinstance(thread_id, str) or not thread_id:
                    clear_control_state()
                    raise AgentAPIError(
                        "The clarification session expired. Please submit the question again."
                    )
                response = api.resume(prompt, thread_id)
            else:
                response = api.ask(prompt, st.session_state.thread_id)
        apply_response_state(response)
        remember_message({"role": "assistant", "response": response})
        st.rerun()
    except AgentAPIError as exc:
        remember_message(
            {
                "role": "assistant",
                "response": {
                    "status": "error",
                    "answer": f"{exc} Check that FastAPI is running, then try again.",
                },
            }
        )
        st.rerun()
