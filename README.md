# Amplifier LSP Bundle

**Code intelligence through Language Server Protocol integration**

The LSP Bundle provides a tool for Language Server Protocol (LSP) operations, enabling code navigation, symbol lookup, and semantic understanding across programming languages.

## What Is This Bundle?

This bundle integrates LSP capabilities into Amplifier sessions, allowing AI agents to:

- **Navigate code** - Jump to definitions, find references, locate implementations
- **Understand symbols** - Get hover documentation, type information, and call hierarchies
- **Explore structure** - List document symbols, search workspace symbols
- **Support multiple languages** - Extensible configuration for any LSP-compatible server

## Components

This bundle provides:

1. **tool-lsp** - Tool module for LSP operations (definition lookup, references, hover, symbols)
2. **lsp-core** - Default behavior configuring the LSP tool
3. **code-navigator** - Agent specialized for code exploration tasks
4. **lsp-general** - Context providing LSP usage guidance

## Installation

### Using the Bundle

Load the bundle directly with Amplifier:

```bash
# Load from git URL
amplifier bundle use git+https://github.com/microsoft/amplifier-bundle-lsp@main
```

### Including in Another Bundle

Add to your bundle's `includes:` section:

```yaml
includes:
  - bundle: lsp
```

## Quick Start

### Basic Usage

```bash
# Start a session with LSP capabilities
amplifier run --bundle lsp

# Navigate code
> Find all references to the Session class in this project
> Go to the definition of the mount() function
> What methods does the Coordinator class have?
```

### LSP Operations

The `tool-lsp` module provides these operations:

| Operation | Description |
|-----------|-------------|
| `goToDefinition` | Find where a symbol is defined |
| `findReferences` | Find all references to a symbol |
| `hover` | Get documentation and type info |
| `documentSymbol` | List all symbols in a file |
| `workspaceSymbol` | Search for symbols across the workspace |
| `goToImplementation` | Find implementations of interfaces |
| `prepareCallHierarchy` | Get call hierarchy at a position |
| `incomingCalls` | Find callers of a function |
| `outgoingCalls` | Find functions called by a function |

## Configuration

### Adding Language Support

The LSP tool requires language server configuration. Use a language-specific bundle (like `lsp-python`) or configure manually:

```yaml
tools:
  - module: tool-lsp
    config:
      languages:
        python:
          command: ["pyright-langserver", "--stdio"]
          file_patterns: ["*.py"]
        typescript:
          command: ["typescript-language-server", "--stdio"]
          file_patterns: ["*.ts", "*.tsx"]
```

### Language-Specific Bundles

For pre-configured language support, use specialized bundles:

- **lsp-python** - Python support via Pyright

## Philosophy

The LSP Bundle follows Amplifier's core principles:

- **Mechanism, not policy** - Provides LSP operations; agents decide how to use them
- **Composable** - Extend with language-specific bundles
- **Observable** - All operations emit events for logging and debugging
- **Minimal** - Core bundle provides just the essentials; language bundles add specifics

## Project Status

**EXPERIMENTAL EXPLORATION**

This is experimental software shared openly but without support infrastructure. See [SUPPORT.md](SUPPORT.md) for details.

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

---

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
