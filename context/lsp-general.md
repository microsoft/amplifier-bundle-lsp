# LSP Tool Usage

You have access to the LSP (Language Server Protocol) tool for code intelligence operations.

## Available Operations

- **goToDefinition**: Find where a symbol is defined
- **findReferences**: Find all references to a symbol
- **hover**: Get documentation and type info for a symbol
- **documentSymbol**: Get all symbols in a document
- **workspaceSymbol**: Search for symbols across the workspace
- **goToImplementation**: Find implementations of interfaces/abstract methods
- **prepareCallHierarchy**: Get call hierarchy item at position
- **incomingCalls**: Find functions that call the target function
- **outgoingCalls**: Find functions called by the target function

## When to Use LSP

Use LSP operations when you need:
- Precise symbol locations (not text search)
- Type information and documentation
- Call graph analysis
- Implementation vs interface resolution

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
