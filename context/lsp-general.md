# LSP Tool Usage

You have access to the LSP (Language Server Protocol) tool for code intelligence operations.

## LSP vs Grep: When to Use Which

| Task | Use LSP | Use Grep |
|------|---------|----------|
| Find all calls to a function | `findReferences` - semantic, finds actual calls | May match comments, strings, similar names |
| Find where a symbol is defined | `goToDefinition` - jumps directly | May find multiple matches |
| Get type info or docstring | `hover` - shows full type signature | Not possible |
| Check for errors after editing | `diagnostics` - compiler errors/warnings | Not possible |
| Rename a symbol everywhere | `rename` - cross-file, semantic | Text-only find/replace, unsafe |
| Find text pattern in files | Not the right tool | Fast text search |
| Search across many files | Slower for bulk search | Fast parallel search |

**Rule of thumb**: Use LSP for semantic understanding, diagnostics, and refactoring. Use grep for text searching.

## When to Reach for LSP First

**Before grepping for a symbol, ask yourself:**
- "Am I looking for usages of this specific function/class?" → `findReferences`
- "Where is this defined?" → `goToDefinition`
- "What does this return/accept?" → `hover`
- "What calls this function?" → `incomingCalls`
- "Did my edit break anything?" → `diagnostics`
- "Rename this symbol everywhere" → `rename`
- "What can I do to fix this error?" → `codeAction`
- "What are the inferred types here?" → `inlayHints`

LSP gives **semantic** results (actual code relationships). Grep gives **text** matches (may include comments, strings, similar names). For code navigation, semantic wins.

## Most Useful Operations

**NAVIGATION (start here):**
1. **hover** — Get type info + docstring at a position (most reliable)
2. **findReferences** — Find all usages of a symbol
3. **goToDefinition** — Jump to where something is defined
4. **incomingCalls** / **outgoingCalls** — Trace call graphs

**VERIFICATION (after editing code):**
5. **diagnostics** — Get compiler errors and warnings for a file

**REFACTORING:**
6. **rename** — Cross-file semantic rename (returns edits to review)
7. **codeAction** — Get suggested fixes and refactorings

**INSPECTION:**
8. **inlayHints** — Get inferred types for a range of code

**EXTENSIONS:**
9. **customRequest** — Send server-specific methods (see language docs)

## After Editing Code

After editing code, use LSP to verify:
1. **diagnostics** — check for errors introduced by the edit
2. **codeAction** — get suggested fixes for any new errors
3. **rename** — if you need to rename a symbol, use this instead of find-and-replace

The diagnostics → codeAction → apply cycle is the AI equivalent of a developer watching compiler output and applying suggested fixes.

## Custom Extensions

Some language servers provide non-standard extensions accessible via **customRequest**. See language-specific context docs for available extensions.

Example: `customRequest(customMethod="server/someExtension", customParams={...})`

## All Available Operations

- **goToDefinition**: Find where a symbol is defined
- **findReferences**: Find all references to a symbol
- **hover**: Get documentation and type info for a symbol
- **documentSymbol**: Get all symbols in a document (may be slow)
- **workspaceSymbol**: Search for symbols across the workspace (needs indexing time)
- **goToImplementation**: Find implementations of interfaces/abstract methods
- **prepareCallHierarchy**: Get call hierarchy item at position
- **incomingCalls**: Find functions that call the target function
- **outgoingCalls**: Find functions called by the target function
- **diagnostics**: Get compiler errors and warnings for a file
- **rename**: Semantic cross-file rename (returns edits, does not apply them)
- **codeAction**: Get suggested fixes and refactorings for a range
- **inlayHints**: Get inferred types and parameter names for a range
- **customRequest**: Send any server-specific LSP method

## Line/Character Numbers

LSP uses 1-based line and character numbers (as shown in editors).

## Example Usage

To find where a function is defined:
```
LSP operation=goToDefinition file_path=/path/to/file.py line=42 character=15
```

To find all callers of a function:
```
LSP operation=incomingCalls file_path=/path/to/file.py line=42 character=15
```

To check for errors after editing:
```
LSP operation=diagnostics file_path=/path/to/file.py
```

## Troubleshooting

If LSP operations fail silently or return empty:
1. **Server not installed**: Check that the language server is installed
2. **Broken installation**: Reinstall the language server if you see interpreter errors
3. **Indexing not complete**: `workspaceSymbol` needs time to index — try `hover` or `findReferences` first
4. **Wrong position**: Ensure cursor is on the symbol name, not whitespace
5. **Server does not support operation**: Not all servers support all operations — the tool returns clear errors when an operation is unsupported
