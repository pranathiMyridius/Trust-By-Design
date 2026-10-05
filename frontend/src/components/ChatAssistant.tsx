import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import "./ChatAssistant.css";

import { AssistantError, sendChat, type ChatReference, type ChatTurn } from "../api/assistant";
import NavIcon from "./NavIcons";
import { friendlyError } from "../utils/errorMessages";

/*
 * Floating, read-only assistant. It can summarise an assessment, list
 * deadlines and explain a risk rating, using only what the signed-in user
 * is already allowed to see. It cannot change anything.
 *
 * The conversation lives in memory only: nothing is stored in the browser,
 * and it is gone when the user signs out or reloads.
 */

interface Message extends ChatTurn {
  references?: ChatReference[];
  failed?: boolean;
}

interface Props {
  /** The assessment the user has open, if any. */
  assessmentId: number | null;
  /** Opens an assessment when the user clicks one the assistant mentioned. */
  onOpenAssessment?: (assessmentId: number) => void;
}

const MAX_LENGTH = 2000;
const MAX_TURNS_SENT = 30;

const SUGGESTIONS_FOR_ASSESSMENT = [
  "Summarise this assessment",
  "What's due, and is anything overdue?",
  "Explain how the risk was rated",
  "What happens next, and who is it waiting on?",
];
const SUGGESTIONS_GENERAL = [
  "What's due in the next 30 days?",
  "Which of my assessments are overdue?",
  "What's waiting on me?",
  "Which assessments are rated high risk?",
];

// Inline **bold** only; everything else is plain text, never HTML.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : (
      part
    )
  );
}

// Paragraphs and "- " / "* " bullet lists -- what the assistant writes.
function renderText(text: string): ReactNode {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];

  const flush = () => {
    if (bullets.length) {
      const items = bullets;
      blocks.push(
        <ul key={`ul-${blocks.length}`}>
          {items.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </ul>
      );
      bullets = [];
    }
  };

  for (const line of text.split("\n")) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    if (bullet) {
      bullets.push(bullet[1]);
      continue;
    }
    flush();
    if (line.trim()) {
      blocks.push(<p key={`p-${blocks.length}`}>{inline(line)}</p>);
    }
  }
  flush();
  return blocks;
}

function errorText(err: unknown): string {
  if (err instanceof AssistantError && (err.status === 429 || err.status === 503)) {
    return err.message;
  }
  return friendlyError(err, "The assistant couldn't answer just now. Please try again.");
}

export default function ChatAssistant({ assessmentId, onOpenAssessment }: Props) {
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, busy]);

  // Stop waiting for an answer nobody will read.
  useEffect(() => () => abortRef.current?.abort(), []);

  async function send(text: string, base: Message[] = messages) {
    const question = text.trim();
    if (!question || busy) {
      return;
    }

    const history: Message[] = [...base, { role: "user", content: question }];
    setMessages(history);
    setDraft("");
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const turns: ChatTurn[] = history
        .filter((message) => !message.failed)
        .slice(-MAX_TURNS_SENT)
        .map(({ role, content }) => ({ role, content }));
      const answer = await sendChat(turns, assessmentId, controller.signal);
      setMessages([
        ...history,
        { role: "assistant", content: answer.reply, references: answer.references },
      ]);
    } catch (err) {
      if (controller.signal.aborted) {
        return;
      }
      setMessages([...history, { role: "assistant", content: errorText(err), failed: true }]);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setBusy(false);
      }
    }
  }

  function newChat() {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setMessages([]);
    setDraft("");
    inputRef.current?.focus();
  }

  function retry() {
    // Drop the failed reply and ask the last question again.
    const withoutError = messages.slice(0, -1);
    const last = withoutError[withoutError.length - 1];
    if (last?.role === "user") {
      void send(last.content, withoutError.slice(0, -1));
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(draft);
    }
  }

  const suggestions = assessmentId !== null ? SUGGESTIONS_FOR_ASSESSMENT : SUGGESTIONS_GENERAL;

  return (
    <div className="chat-assistant">
      {open && (
        <section
          className="chat-panel"
          role="dialog"
          aria-label="Assistant"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setOpen(false);
            }
          }}
        >
          <header className="chat-header">
            <span className="chat-header-icon" aria-hidden="true">
              <NavIcon name="bot" size={24} />
            </span>
            <h2>Assistant</h2>
            <div className="chat-header-actions">
              <div className="chat-menu-wrap">
                <button
                  type="button"
                  className="chat-icon-button"
                  aria-label="More options"
                  aria-haspopup="menu"
                  aria-expanded={menuOpen}
                  onClick={() => setMenuOpen((value) => !value)}
                >
                  <span aria-hidden="true">•••</span>
                </button>
                {menuOpen && (
                  <div className="chat-menu" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      disabled={messages.length === 0}
                      onClick={() => {
                        setMenuOpen(false);
                        newChat();
                      }}
                    >
                      New chat
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        newChat();
                        setOpen(false);
                      }}
                    >
                      End chat
                    </button>
                  </div>
                )}
              </div>
              <button
                type="button"
                className="chat-icon-button"
                aria-label="Minimise assistant"
                onClick={() => {
                  setMenuOpen(false);
                  setOpen(false);
                }}
              >
                <span aria-hidden="true">&#8212;</span>
              </button>
            </div>
          </header>

          <div className="chat-messages" role="log" aria-live="polite" aria-relevant="additions">
            {/* Always the first message, so the conversation opens with a greeting. */}
            <div className="chat-message chat-from-assistant">
              <span className="chat-sr-only">Assistant: </span>
              <div className="chat-bubble">
                <p>Hi, I&apos;m an AI Assistant. How can I help you today?</p>
              </div>
            </div>

            {messages.length === 0 && (
              <div className="chat-welcome">
                <div className="chat-suggestions">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className="chat-chip"
                      onClick={() => void send(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message, index) => (
              <div
                key={index}
                className={`chat-message chat-from-${message.role}${message.failed ? " chat-failed" : ""}`}
              >
                <span className="chat-sr-only">{message.role === "user" ? "You: " : "Assistant: "}</span>
                <div className="chat-bubble">{renderText(message.content)}</div>

                {message.failed && index === messages.length - 1 && (
                  <button type="button" className="chat-link-button" onClick={retry}>
                    Try again
                  </button>
                )}

                {!!message.references?.length && onOpenAssessment && (
                  <div className="chat-references">
                    {message.references.map((reference) => (
                      <button
                        key={reference.id}
                        type="button"
                        className="chat-reference"
                        onClick={() => {
                          onOpenAssessment(reference.id);
                          setOpen(false);
                        }}
                        title={reference.title ?? undefined}
                      >
                        {reference.reference_id ?? `#${reference.id}`}
                        {reference.title ? ` · ${reference.title}` : ""}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}

            {busy && (
              <div className="chat-message chat-from-assistant" aria-label="The assistant is working">
                <div className="chat-bubble chat-typing" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form
            className="chat-form"
            onSubmit={(event) => {
              event.preventDefault();
              void send(draft);
            }}
          >
            <label className="chat-sr-only" htmlFor="chat-input">
              Ask the assistant
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              value={draft}
              maxLength={MAX_LENGTH}
              rows={1}
              placeholder="Message..."
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
            />
            <button
              type="submit"
              className="chat-send"
              aria-label="Send message"
              disabled={busy || !draft.trim()}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M21 3 10 14" />
                <path d="m21 3-7 18-4-7-7-4 18-7Z" />
              </svg>
            </button>
          </form>
          <p className="chat-footnote">
            Read-only. Answers from assessments you can access
            {assessmentId !== null ? ` · viewing #${assessmentId}` : ""}. AI-generated, so check
            anything important in the assessment itself.
          </p>
        </section>
      )}

      <button
        type="button"
        className="chat-launcher"
        aria-expanded={open}
        aria-label={open ? "Close assistant" : "Open assistant"}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "×" : <NavIcon name="bot" size={26} />}
      </button>
    </div>
  );
}
