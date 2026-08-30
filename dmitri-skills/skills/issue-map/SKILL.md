---
name: issue-map
description: Refresh docs/open-issue-map.md. Facts come from gh, judgment stays in the document.
disable-model-invocation: true
---

# Issue map

Rewrite `docs/open-issue-map.md`.
The document holds two kinds of content, and each kind has one owner.

**Facts** belong to GitHub.
Which issues are open, their titles, their labels, their native dependencies, and which of them have an open pull request.
Write a fact only when you read it from `gh` in this run.

**Judgment** belongs to the document.
Thread names, thread membership, hard edges, soft edges, the reason on each edge, and the issue type.
GitHub holds none of it.
The current document is the only copy, so carry every thread name, every edge, and every reason forward into the new document.

## Step 1 - read the judgment

Read `docs/open-issue-map.md`.
Extract the judgment into a working list:

- Each thread letter, its name, and its member issue numbers.
- Each hard edge and each soft edge, with the reason if the document gives one.
- Each issue type from the summary table: PRD, Work, Decision, Record, Discovery, or Bug.

## Step 2 - fetch the facts

Run these two commands.
Two calls answer everything.
Do not open issues one at a time.

```bash
gh issue list --state open --limit 200 \
  --json number,title,labels,url,blockedBy,blocking
gh pr list --state open --limit 100 \
  --json number,title,url,headRefName,body,closingIssuesReferences
```

Link each pull request to an issue by the first rule that matches:

1. `closingIssuesReferences`, when it is not empty.
2. The number in `headRefName`, for example `issue-467-phase-1` gives #467.
3. The first `#NNN` in the title, then in the body.

Record a pull request as unlinked when no rule matches.

## Step 3 - reconcile

- **New issue**: open on GitHub and absent from the document.
  Put it in the thread that clearly fits.
  Put it under a thread named `Unfiled` when no thread clearly fits.
- **Closed issue**: present in the document and not open on GitHub.
  Remove its node from the thread.
  Move every edge that touched it to `Stale relations`, and keep its reason.
- **Disagreement**: GitHub holds a `blockedBy` or `blocking` link that the document does not draw, or the document draws a hard edge that GitHub does not hold.
  Keep the document edge.
  Report the disagreement.

## Step 4 - write the document

Write `docs/open-issue-map.md` in this order.

1. A title and one line with today's date.
2. `## Open pull requests`.
   One line for each open pull request: the issue it belongs to, the pull request number, the title, and the URL.
   Write `None` when there are no open pull requests.
3. One `## Thread X - name` section for each thread.
   Each section holds one Mermaid `flowchart TD` with only that thread's issues, and then the prose paragraph from the old document.
   Keep the `classDef` block and the `#NNN Title` node label format from the old document.
   Do not draw a subgraph, and do not draw an invisible `~~~` link.
   Refresh the class of each node from the labels: `bug` gives the bug class, `ready-for-agent` gives the ready class, a PRD or a decision keeps its class from the old document, and anything else gets the work class.
4. `## Cross-thread relations`.
   A plain list, one line for each edge that crosses a thread boundary, with its reason.
   Write every one of them here.
   A list has no readability limit, so drop nothing.
5. `## Stale relations`, only when Step 3 produced one.
6. `## Summary table` with the columns Issue, Thread, Type, Blocked by, and Blocks.
7. The two legends from the old document, for the line styles and the colors.

## Step 5 - verify and report

The document is correct when both statements are true:

- Every open issue number from `gh` appears exactly once in a thread section and once in the summary table.
- Every issue number in the document is open on GitHub.

Check both before you report.

Then report to the user, and report nothing else:

- Issues added, with the thread you chose for each.
- Issues removed because they closed.
- Edges moved to `Stale relations`.
- Disagreements between GitHub and the document.
- `No change` when all four lists are empty.
