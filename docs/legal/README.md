# Legal documents

| Document | Who it's for | Shown in |
|---|---|---|
| [Privacy Policy](privacy-policy.md) | Everyone | All apps (sign-in screen, More / Security), web pages, portal footer |
| [Terms of Service](terms-of-service.md) | Customers | Customer app, sign-in screen, payment pages |
| [Merchant Terms](merchant-terms.md) | Businesses | Business app, merchant portal |
| [Agent Terms](agent-terms.md) | Agents | Agent app |
| [Complaints and Disputes Policy](complaints-and-disputes.md) | Everyone | All apps, web pages |
| [Acceptable Use Policy](acceptable-use.md) | Everyone | All apps, web pages |

**Status:** every document is a DRAFT. Each must be reviewed by Ghanaian counsel and the Data Protection Officer before launch. Resolve every [VERIFY] item at the same time.

**One source of truth.** These files are the originals. The backend serves the same text at `/legal/<name>`, for example `/legal/privacy-policy`, and the apps link to those pages. The backend keeps a copy in `backend/legal/` because the server image doesn't include this folder. A test fails if the two ever differ. After editing a document here, copy it there:

```bash
cp docs/legal/*.md backend/legal/
```

**Versioning.** Bump the version line in a document whenever its meaning changes. Material changes to customer terms must be announced in the app before they take effect.
