# GitHub Copilot Agent Instructions

## Task Completion Policy

For every coding task, work from start to finish. Do not stop after partially implementing the requested changes.

### TODO Management

1. Before making significant changes, analyze the request and create a complete TODO/task list for all required work.
2. Break large tasks into clear, actionable steps.
3. Execute the TODO items sequentially.
4. Keep the TODO list updated throughout the task.
5. Mark an item as completed only after the corresponding work has actually been implemented and verified.
6. Never mark an item as completed merely because the code appears to be finished.
7. Before finishing, review the TODO list and ensure there are no unfinished, skipped, or incorrectly completed items.

### Implementation

* Inspect the existing codebase before making changes.
* Follow the existing architecture, conventions, patterns, and coding style.
* Do not unnecessarily rewrite or remove existing functionality.
* Implement the requested functionality completely, including related edge cases.
* If the task requires changes in multiple files or components, complete all necessary changes rather than stopping at the first successful modification.
* Do not leave placeholder implementations, unnecessary TODO comments, dead code, or incomplete sections.

### Verification

After implementation:

1. Run the relevant tests.
2. Run the project's build command when applicable.
3. Run linting and formatting checks when applicable.
4. Run type checking when applicable.
5. Inspect the results for errors, warnings, and regressions.
6. If anything fails, diagnose the cause, fix it, and run the relevant checks again.
7. Continue this process until the implementation is verified.

### Final Review

Before declaring the task complete:

* Re-read the original user request.
* Verify every requirement against the actual implementation.
* Review the TODO list.
* Search for unfinished TODO items related to the task.
* Check for compilation, type, lint, test, and build errors.
* Check for obvious regressions.
* Verify that the final implementation actually works rather than merely compiling.
* If something remains incomplete, continue working on it instead of reporting it as completed.

### Stopping Rule

Do not stop merely because the requested code has been written.

Do not stop after explaining what should be done.

Do not stop with a partially completed TODO list.

Do not stop when tests fail.

Do not stop when build, lint, or type checks fail.

When you have sufficient tools and permissions to complete the work, perform the remaining work yourself.

Only declare the task complete when all required TODO items have been implemented and verified.

If a task genuinely cannot be completed because required information, credentials, permissions, an external service, or a user decision is unavailable, clearly identify the blocking issue and stop at that point.
