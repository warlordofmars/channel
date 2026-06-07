// Copyright (c) 2026 John Carter. All rights reserved.
//
// Discriminated-union renderer for tool results in the Conversation
// view. Introduced by #181 PR-3 as the seed; #182 adds the
// kind="web-search-citations" branch, #183 adds kind="code-output".
// Strategy spec policy P3.
//
// Until rich branches land, the default branch is a plain monospace
// summary — what the chassis exit test (current_time) needs.

export default function ToolResultBlock({ kind, summary }) {
  // Future branches will switch on `kind`. Today only the default
  // text-summary path renders.
  return (
    <div className="tool-result-block" data-kind={kind || "default"}>
      <pre className="tool-result-summary">{summary}</pre>
    </div>
  );
}
