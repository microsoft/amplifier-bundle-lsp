# LSP Tool Usage

You have access to the LSP (Language Server Protocol) tool for code intelligence operations.

## LSP vs Grep: When to Use Which

| Task | Use LSP | Use Grep |
|------|---------|----------|
| Find all calls to a function | ✅ `findReferences` - semantic, finds actual calls | ❌ May match comments, strings, similar names |
| Find where a symbol is defined | ✅ `goToDefinition` - jumps directly | ❌ May find multiple matches |
| Get type info or docstring | ✅ `hover` - shows full type signature | ❌ Not possible |
| Find text pattern in files | ❌ Not the right tool | ✅ Fast text search |
| Search across many files | ❌ Slower for bulk search | ✅ Fast parallel search |

**Rule of thumb**: Use LSP for navigation and understanding, grep for searching.

## When to Reach for LSP First

**Before grepping for a symbol, ask yourself:**
- "Am I looking for usages of this specific function/class?" → `findReferences`
- "Where is this defined?" → `goToDefinition`
- "What does this return/accept?" → `hover`
- "What calls this function?" → `incomingCalls`

LSP gives **semantic** results (actual code relationships). Grep gives **text** matches (may include comments, strings, similar names). For code navigation, semantic wins.

## Most Useful Operations (Start Here)

1. **hover** - Get type info + docstring at a position (most reliable)
2. **findReferences** - Find all usages of a symbol (very useful)
3. **goToDefinition** - Jump to where something is defined
4. **incomingCalls** / **outgoingCalls** - Trace call graphs

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

## Troubleshooting

If LSP operations fail silently or return empty:
1. **Server not installed**: Check that the language server is installed (e.g., `pyright --version`)
2. **Broken installation**: If you see "bad interpreter" errors, reinstall via npm: `npm install -g pyright`
3. **Indexing not complete**: `workspaceSymbol` needs time to index - try `hover` or `findReferences` first
4. **Wrong position**: Ensure cursor is on the symbol name, not whitespace
