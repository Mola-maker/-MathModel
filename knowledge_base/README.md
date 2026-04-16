# Knowledge Base

This directory is the project's background knowledge base.

## What is stored here
- `modeling_sources.json`: literature/source snapshot used by modeling subagent
- `github_sources.json`: live GitHub repository search snapshot used by coding subagent

## Source policy
- CNKI advanced search and Web of Science advanced search usually require institutional login/API authorization.
- The project therefore uses open sources by default:
  - OpenAlex for literature discovery
  - GitHub REST API for code repositories

## Runtime write path
Default path: `E:/mathmodel/knowledge_base`
Can be overridden with env var: `KNOWLEDGE_BASE_DIR`
