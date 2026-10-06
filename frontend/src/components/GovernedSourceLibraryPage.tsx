import { useCallback, useEffect, useRef, useState } from "react";

import {
  getMeta,
  listRecords,
  searchPassages,
  seedCatalog,
  type LibraryMeta,
  type Passage,
  type RecordFilters,
  type RecordPage,
} from "../api/sourceLibrary";
import { friendlyError } from "../utils/errorMessages";
import SourceDetailView from "./SourceDetailView";
import SourceFormModal from "./SourceFormModal";
import SourceLibraryPage from "./SourceLibraryPage";
import { STATUS_LABEL, formatDate } from "./sourceLibraryUi";
import "./SourceLibrary.css";
import "./SourceLibraryGoverned.css";

type Tab = "sources" | "search" | "help";

const STATUS_FILTERS: [string, string][] = [
  ["", "All"],
  ["APPROVED", "Approved"],
  ["IN_REVIEW", "In review"],
  ["DRAFT", "Draft"],
  ["REJECTED", "Rejected"],
  ["RETIRED", "Retired"],
  ["OUTDATED", "Past review date"],
];

const PAGE_SIZES = [10, 20, 50];

/**
 * The Source Library: governed regulatory and internal-policy sources.
 * Maintainers add sources and versions; an authorised compliance reviewer
 * approves them; only approved versions are used in risk assessments.
 */
export default function GovernedSourceLibraryPage() {
  const [meta, setMeta] = useState<LibraryMeta | null>(null);
  const [tab, setTab] = useState<Tab>("sources");
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [data, setData] = useState<RecordPage | null>(null);
  const [loadedFor, setLoadedFor] = useState<RecordFilters | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [filters, setFilters] = useState<RecordFilters>({ sort: "title", page: 1, page_size: 20 });
  const [searchText, setSearchText] = useState("");
  const request = useRef(0);

  useEffect(() => {
    getMeta()
      .then(setMeta)
      .catch((err) => setError(friendlyError(err, "The Source Library couldn't be loaded.")));
  }, []);

  // Typing in the search box filters after a short pause.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      setFilters((current) => (current.q === (searchText.trim() || undefined) ? current : { ...current, q: searchText.trim() || undefined, page: 1 }));
    }, 300);
    return () => window.clearTimeout(handle);
  }, [searchText]);

  const load = useCallback(() => {
    const mine = ++request.current;
    listRecords(filters)
      .then((page) => {
        if (mine !== request.current) return;
        setData(page);
        setLoadedFor(filters);
        setError("");
      })
      .catch((err) => {
        if (mine === request.current) setError(friendlyError(err, "The library couldn't be loaded."));
      })
      .finally(() => undefined);
  }, [filters]);

  const loading = loadedFor !== filters;

  useEffect(load, [load]);

  function setFilter(key: keyof RecordFilters, value: string) {
    setFilters((current) => ({ ...current, [key]: value || undefined, page: 1 }));
  }

  const hasFilters = Boolean(filters.q || filters.status || filters.category || filters.jurisdiction || filters.authority);
  const summary = data?.summary;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const from = data && data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const to = data ? Math.min(data.total, data.page * data.page_size) : 0;

  if (!meta) {
    return (
      <div className="sl-page">
        {error ? (
          <p className="sl-banner sl-banner--error" role="alert">
            <span aria-hidden="true">✕</span>
            <span>{error}</span>
          </p>
        ) : (
          <p className="sl-empty">Loading the Source Library…</p>
        )}
      </div>
    );
  }

  if (selected) {
    return (
      <div className="sl-page">
        <SourceDetailView recordId={selected} meta={meta} notice={message} onBack={() => { setSelected(null); setMessage(""); }} onChanged={load} />
      </div>
    );
  }

  return (
    <div className="sl-page">
      <header className="sl-header">
        <div>
          <h2>Source Library</h2>
          <p>
            Regulations, guidance, sanctions resources, internal policies, procedures and methodologies. A source is used in risk
            assessments only through an approved version, reviewed by an authorised compliance reviewer
            {meta.reviewer_rule ? ` (${meta.reviewer_rule})` : ""}. New versions never replace an approved one until they are approved.
          </p>
        </div>
        {meta.can_maintain && (
          <div className="sl-header__actions">
            <button type="button" className="sl-btn sl-btn--primary" onClick={() => setAdding(true)}>
              <span aria-hidden="true">＋</span> Add source
            </button>
          </div>
        )}
      </header>

      <div className="slg-tabs" role="tablist" aria-label="Source Library sections">
        {(
          [
            ["sources", "Sources"],
            ["search", "Search passages"],
            ...(meta.can_maintain ? [["help", "Help articles"]] : []),
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={`slg-tab${tab === key ? " slg-tab--active" : ""}`} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {error && (
        <p className="sl-banner sl-banner--error" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{error}</span>
          <button type="button" className="sl-btn sl-btn--small sl-btn--secondary slg-retry" onClick={load}>
            Retry
          </button>
        </p>
      )}
      {message && (
        <p className="sl-banner sl-banner--ok" role="status">
          <span aria-hidden="true">✓</span>
          <span>{message}</span>
        </p>
      )}

      {tab === "help" && <SourceLibraryPage canManage={meta.can_maintain} />}
      {tab === "search" && <PassageSearch meta={meta} />}

      {tab === "sources" && (
        <>
          <section className="sl-stats" aria-label="Library summary">
            {(
              [
                ["Approved", summary?.approved, "APPROVED", false],
                ["In review", summary?.in_review, "IN_REVIEW", false],
                ["Drafts", summary?.draft, "DRAFT", false],
                ["Past review date", summary?.outdated, "OUTDATED", true],
              ] as [string, number | undefined, string, boolean][]
            )
              .filter(([, , status]) => meta.can_view_all || status === "APPROVED" || status === "OUTDATED")
              .map(([label, count, status, warn]) => (
                <button
                  key={label}
                  type="button"
                  className={`sl-stat slg-stat${warn && count ? " sl-stat--warn" : ""}${filters.status === status ? " slg-stat--active" : ""}`}
                  aria-pressed={filters.status === status}
                  onClick={() => setFilter("status", filters.status === status ? "" : status)}
                >
                  <span>{label}</span>
                  <strong>{count ?? "–"}</strong>
                </button>
              ))}
          </section>

          <section className="sl-card" aria-labelledby="slg-list-title">
            <h3 className="sl-card__title" id="slg-list-title">
              Sources {data ? `(${data.total})` : ""}
            </h3>
            <p className="sl-card__subtitle">Open a source to see its versions, approval history and audit timeline.</p>

            <div className="slg-filterbar">
              <input
                type="search"
                className="sl-list-search slg-search"
                aria-label="Search sources"
                placeholder="Search title, ID, authority, owner or topic…"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
              />
              {meta.can_view_all && (
                <select aria-label="Filter by status" value={filters.status ?? ""} onChange={(event) => setFilter("status", event.target.value)}>
                  {STATUS_FILTERS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {value ? label : "All statuses"}
                    </option>
                  ))}
                </select>
              )}
              <select aria-label="Filter by category" value={filters.category ?? ""} onChange={(event) => setFilter("category", event.target.value)}>
                <option value="">All categories</option>
                {meta.categories.map((category) => (
                  <option key={category.value} value={category.value}>
                    {category.label}
                  </option>
                ))}
              </select>
              <select aria-label="Filter by jurisdiction" value={filters.jurisdiction ?? ""} onChange={(event) => setFilter("jurisdiction", event.target.value)}>
                <option value="">All jurisdictions</option>
                {(data?.jurisdictions ?? []).map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
              <select aria-label="Filter by authority" value={filters.authority ?? ""} onChange={(event) => setFilter("authority", event.target.value)}>
                <option value="">All authorities</option>
                {(data?.authorities ?? []).map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
              <select aria-label="Sort by" value={filters.sort ?? "title"} onChange={(event) => setFilter("sort", event.target.value)}>
                <option value="title">Sort: title</option>
                <option value="code">Sort: source ID</option>
                <option value="updated">Sort: recently updated</option>
              </select>
              {hasFilters && (
                <button
                  type="button"
                  className="sl-btn sl-btn--small sl-btn--secondary"
                  onClick={() => {
                    setSearchText("");
                    setFilters({ sort: "title", page: 1, page_size: filters.page_size });
                  }}
                >
                  Clear filters
                </button>
              )}
            </div>

            {loading && !data ? (
              <p className="sl-empty">Loading sources…</p>
            ) : data && data.total === 0 ? (
              <div className="sl-empty">
                {hasFilters ? (
                  "No sources match these filters."
                ) : (
                  <>
                    <p>The library is empty.</p>
                    {meta.can_maintain && (
                      <button
                        type="button"
                        className="sl-btn sl-btn--secondary"
                        onClick={async () => {
                          try {
                            const result = await seedCatalog();
                            setMessage(`Loaded ${result.created} starter source(s) as drafts. Review and approve each before use.`);
                            load();
                          } catch (err) {
                            setError(friendlyError(err, "The starter catalogue couldn't be loaded."));
                          }
                        }}
                      >
                        Load the starter catalogue
                      </button>
                    )}
                  </>
                )}
              </div>
            ) : (
              <div className="sl-table-wrap" aria-busy={loading}>
                <table className="sl-table slg-table">
                  <thead>
                    <tr>
                      <th scope="col">Source</th>
                      <th scope="col">Category</th>
                      <th scope="col">Jurisdiction</th>
                      <th scope="col">Version</th>
                      <th scope="col">Review by</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data?.items ?? []).map((item) => (
                      <tr key={item.id} className="slg-row">
                        <td>
                          <button type="button" className="slg-linkbtn" aria-label={`Open source ${item.source_code} ${item.title}`} onClick={() => setSelected(item.id)}>
                            {item.title}
                          </button>
                          <div className="sl-sub">
                            {item.source_code} · {item.authority}
                          </div>
                        </td>
                        <td>{item.category_label}</td>
                        <td>{item.jurisdiction ?? "All"}</td>
                        <td>
                          {item.current_version?.version_label ?? "—"}
                          {item.pending_version && item.library_status === "APPROVED" && (
                            <div className="sl-sub">
                              <span className="sl-pill sl-pill--draft">v{item.pending_version.version_label} {STATUS_LABEL[item.pending_version.status].toLowerCase()}</span>
                            </div>
                          )}
                        </td>
                        <td>
                          {formatDate(item.current_version?.review_date)}
                          {item.outdated && (
                            <div className="sl-sub">
                              <span className="sl-pill sl-pill--outdated">⚠ Outdated</span>
                            </div>
                          )}
                        </td>
                        <td>
                          <span className={`sl-pill sl-pill--${item.library_status.toLowerCase().replace("_", "-")}`}>{STATUS_LABEL[item.library_status]}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {data && data.total > 0 && (
              <nav className="slg-pager" aria-label="Pagination">
                <span>
                  Showing {from}–{to} of {data.total}
                </span>
                <label>
                  Per page{" "}
                  <select value={data.page_size} onChange={(event) => setFilters((c) => ({ ...c, page_size: Number(event.target.value), page: 1 }))}>
                    {PAGE_SIZES.map((size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" disabled={data.page <= 1} onClick={() => setFilters((c) => ({ ...c, page: data.page - 1 }))}>
                  Previous
                </button>
                <span>
                  Page {data.page} of {pages}
                </span>
                <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" disabled={data.page >= pages} onClick={() => setFilters((c) => ({ ...c, page: data.page + 1 }))}>
                  Next
                </button>
              </nav>
            )}
          </section>
        </>
      )}

      {adding && (
        <SourceFormModal
          mode={{ kind: "create" }}
          meta={meta}
          onClose={() => setAdding(false)}
          onSaved={(saved, text) => {
            setAdding(false);
            setMessage(text);
            load();
            if (saved) setSelected(saved.id);
          }}
        />
      )}
    </div>
  );
}

function PassageSearch({ meta }: { meta: LibraryMeta }) {
  const [query, setQuery] = useState("");
  const [jurisdictions, setJurisdictions] = useState("");
  const [category, setCategory] = useState("");
  const [results, setResults] = useState<Passage[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");

  return (
    <section className="sl-card" aria-labelledby="slg-search-title">
      <h3 className="sl-card__title" id="slg-search-title">
        Search approved passages
      </h3>
      <p className="sl-card__subtitle">
        Only approved, in-effect versions are searched — the same passages risk assessments are given. Leave jurisdictions blank for the home
        jurisdiction{meta.home_jurisdiction ? ` (${meta.home_jurisdiction})` : ""} and global sources, or name the countries involved.
      </p>
      <form
        className="slg-searchform"
        onSubmit={(event) => {
          event.preventDefault();
          if (query.trim().length < 2) return;
          setSearching(true);
          setError("");
          searchPassages(query.trim(), jurisdictions, category || undefined)
            .then(setResults)
            .catch((err) => setError(friendlyError(err, "The search failed.")))
            .finally(() => setSearching(false));
        }}
      >
        <input aria-label="Search approved passages" placeholder="e.g. beneficial ownership for legal entities" value={query} onChange={(event) => setQuery(event.target.value)} />
        <input aria-label="Jurisdictions" placeholder="Jurisdictions, e.g. Germany, Poland" value={jurisdictions} onChange={(event) => setJurisdictions(event.target.value)} />
        <select aria-label="Category" value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="">All categories</option>
          {meta.categories.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <button type="submit" className="sl-btn sl-btn--primary" disabled={searching || query.trim().length < 2}>
          {searching ? "Searching…" : "Search"}
        </button>
      </form>
      {error && (
        <p className="sl-banner sl-banner--error" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{error}</span>
        </p>
      )}
      {results &&
        (results.length === 0 ? (
          <p className="sl-empty">No approved passage matches “{query}”.</p>
        ) : (
          <ul className="sl-results">
            {results.map((result) => (
              <li key={result.chunk_id} className="sl-result">
                <div className="sl-result__meta">
                  <strong>
                    {result.title} · {result.version_label}
                  </strong>
                  <span>{result.source_code}</span>
                  {result.location && <span>· {result.location}</span>}
                  {result.outdated && <span className="sl-pill sl-pill--outdated">⚠ Past review date</span>}
                </div>
                <blockquote>“{result.text}”</blockquote>
              </li>
            ))}
          </ul>
        ))}
    </section>
  );
}
