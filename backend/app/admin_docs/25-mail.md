key: mail
title: Mail
order: 25
screens: /mail
api: /admin-api/mail
summary: The team's own e-mail program inside the admin — the company's mailboxes like in Outlook: folders, reading, search, writing with signature and files, drafts, undo send, scheduled send, team access.
---
## What it is for

Mail is a complete e-mail program inside the admin, so you and your team answer, file and send the company's mail without leaving the admin. It works with any mailbox that offers IMAP and SMTP (IONOS, Strato, GMX, WEB.DE, T-Online, Gmail with an app password, …). Everything you do here happens on the mail server at once: what you read, flag, move or delete here looks the same on your phone and in webmail, and the other way round.

## Adding an account

Open **Mail → ⚙ (settings) → Accounts → Add account**. Enter the address, your name as recipients see it, and the password. For the usual providers the servers are filled in for you; **Show server settings** shows them (and is where you enter your own). **Test connection** signs in to both servers without sending or reading anything. The password is stored encrypted and never shown again.

The **marketing mailbox** from [Mailboxes](docs:mailboxes) is added with one click (**Add marketing mailbox**): it keeps its servers and password there, so they are kept in one place.

Each account has a **colour**, the **language of replies** (German: "AW:", "WG:" and "Am … schrieb …"; English: "Re:", "Fwd:") and how far back the first sync reaches (**Mail kept here**, e.g. 90 days). Older mail stays on the server; **Get older mail from the server** at the end of a folder fetches it.

## Reading

- The left side shows **Favourites** (all inboxes, unread, flagged, your drafts, the outbox) and each account with its folders (Inbox, Drafts, Sent, Archive, Junk, Trash and your own folders).
- The list shows unread mail in bold with a bar, flags, files and whether you answered or forwarded it. Filters: **All, Unread, Flagged, Files**.
- A mail opens on the right: who wrote to whom, the files to download, and the **earlier mails of the conversation**.
- **Pictures from the internet are blocked** until you click **Show pictures** (or **Always from …** for a sender you trust): senders use them to see who opened a mail. The text is shown in a protected frame where nothing in a mail can run.
- **Search** looks in subject, sender, recipients and text, in this folder or in all mail. It understands `from:`, `to:`, `subject:`, `has:attachment`, `is:unread`, `is:flagged`.

## Filing

Select one mail or several (the box, Ctrl-click, Shift-click) and **mark read/unread, flag, archive, move, mark as junk or delete** — or drag them onto a folder. Deleted mail goes to the Trash first; from the Trash it is gone for good (you are asked). After archiving, moving or deleting, **Undo** in the message at the bottom moves it straight back. Folders are made, renamed and deleted with **…** next to a folder (a new folder inside one, too); Trash and Junk can be emptied there.

## Writing

**New mail** (or N), **Reply**, **Reply all**, **Forward**. The draft starts with **your signature** for that account, a reply with the quoted mail, a forward with its files.

- **To, Cc, Bcc** suggest addresses while you type: people you wrote with.
- The text can be formatted (bold, lists, colours, links, quotes), pictures can be pasted or put in the text, files are attached with **Attach** or dropped onto the mail. Programs are refused; every file is checked by the virus scanner.
- The draft is **saved as you write**. Closing it keeps it in **My drafts** and in the account's **Drafts folder** (so it is on your phone too); a draft started on the phone opens here with **Edit draft**.
- **Send** (or Ctrl+Enter) waits a few seconds so you can still **Undo** — the mail comes back as it was. **Schedule send** (the arrow next to Send) sends it at a time you choose; until then it waits in the **Outbox**, where you can stop it.
- A sent mail is put into the account's **Sent folder** and the mail you answered is marked as answered. If the server refuses a mail, it stays in the Outbox with the reason and **Try again**.

## Signatures and preferences

**⚙ → Signatures**: your own signature in each account (each person on the team has theirs). **⚙ → Preferences**: how long Undo waits, whether a mail is marked read when you open it, and the senders whose pictures are always shown — kept in this browser.

## Team access

Whoever adds an account manages it; an owner can manage every account. **⚙ → Team access** decides who on the team sees an account, who may **send** from it and who may **manage** it (change its settings and this list). Someone without access does not see the account at all. Adding, changing and removing accounts and access is recorded in the [admin log](docs:admin-log); what people read and write is not.

## Keyboard

N new mail · R reply · A reply all · F forward · E archive · Del delete · U unread · S flag · J/K or ↓/↑ next/previous · / search · Ctrl+Enter send · Esc close.
