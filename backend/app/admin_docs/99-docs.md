key: docs
title: Docs
order: 99
screens: /docs, /docs/:key
api: /admin-api/docs
summary: This documentation: every admin page explained, with search; the "?" opens the part for the page you are on.
---
## What it is for

One place that explains every admin page and every button: what it does, who may use it, and what happens afterwards. Nia reads the same text, so she explains things the same way.

## How to use it

- **Search** at the top left finds every section that contains all the words you type, with the matching line.
- The **"?"** next to the bell opens the section for the page you are on, without leaving it. **Open in Docs** shows it here.
- Links in the text open the admin page they name, or another section.

## Keeping it right

Each section is a text file in the app (`backend/app/admin_docs`). The app's tests fail if an admin page or an admin server function has no section, so a new feature comes with its documentation.
