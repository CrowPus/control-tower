key: social
title: Marketing
order: 34
screens: /marketing, /social
api: /admin-api/marketing
summary: The Marketing Lead's campaign plan, the social media calendar, the picture studio, the videos with the company's character, results per network, connected Facebook/Instagram/TikTok/LinkedIn/YouTube accounts and the brand kit.
---
## What it is for

Marketing is run by the agents you give the marketing tools — for example a **Marketing Lead** who plans, a **Social Media Manager** who makes the posts and pictures, and a **Graphic Designer** who makes short videos with the company's character (all three are starter templates). Marketing only posts publicly. You give clearance in [Clearance](docs:approvals); on connected networks the install then **publishes by itself** at the planned time and reads what each post achieved.

## Plan

The Marketing Lead proposes one campaign plan per period (usually two or four weeks): goals with a number and where we stand now, the audiences (who they are, where, and why now), the campaigns per network with their message, and a brief for each specialist. When you clear it, it becomes the active plan and each brief goes to its agent. A newer plan proposal replaces one still waiting.

## Calendar

Every cleared post, one per network:

| Status | Meaning |
|---|---|
| Scheduled | Goes out by itself at its time on the connected account. **Publish now** sends it at once; **Edit** changes text and time. |
| Needs your video | A video post: the agent wrote the **video script** (hook, scenes with seconds, what the camera shows, text on screen, voice-over, music, call to action). Film it, put the file on Google Drive or Dropbox (shared with "anyone with the link") and paste the link: the post is scheduled. The file is fetched when it goes out and not kept. |
| Post by hand | The network is not connected (or X): **Copy text**, post it yourself, then **Posted**. |
| Failed | The network refused; the reason is shown. **Try again** after fixing it. |
| Posted | With its link and its numbers (reach, views, likes, comments, shares, saves, clicks). |

## Studio

**Make a picture**: describe the scene (who, doing what, where, light, mood), choose the format (4:5 for Instagram/Facebook feeds, 1:1, 9:16 stories, 16:9 LinkedIn/YouTube), optionally a headline and a line under it — they are set on a band in your brand colours with your logo. Every AI-made picture carries the **AI label** (Brand → AI label, "AI-generated" unless you change it); when it shows people, the post says so too (your decision: AI people allowed, always labelled). Pictures never show other companies' brands or logos, real or famous persons, or fake testimonials.

Each picture costs about $0.07 (Best quality about $0.14) from your **monthly picture budget** (Brand); without a budget no picture is made, by the agents or by you. **Upload** real photos and your logo (**Use as logo**); **add a video** you made by its link.

## Character

The company's **own character**: one figure that appears in every video, so people come to know it and know it is you. Upload up to **three clear pictures** of it (the face well visible — ideally front, side and full body; the first is the main one) and describe it: its name, who it is, **what never changes** (face, hair, skin, build, age), its personality, **whether it speaks** (off: a silent character — action, facial expression, music and sound effects; the message is on the end card and in the caption), its voice if it speaks, and your rules (what it always or never does, says or wears). The video model gets the pictures with every scene, so the **person stays the same while its clothes, places and situations change**. A character picture cannot be deleted from the library while it is used.

## Videos

The **Graphic Designer** reads the Marketing Lead's brief, what worked before and what is working on TikTok and Reels for your audiences, and proposes **video concepts**: the idea, the hook (the first two seconds), two to four scenes of 8 seconds (what the camera shows, one simple action, the character's face, the music and sound effects — and the exact English words only if the character speaks), the end card, the caption and hashtags, and the **first frame of every scene** (a picture of the character in the video's outfit and place — the video starts from exactly that picture). You see it in [Clearance](docs:approvals) with its price and the statements about the company it makes (only from **Brand → Facts for customers**). **Nothing is made before you clear the concept.**

After clearance the video is made by itself, scene by scene (each takes from seconds to a few minutes; the card shows the progress). The scenes are joined with the small AI label and your end card (logo, headline) — nothing else is laid over the picture; the name is said the way you set in **Brand → Name said as** — into one vertical video (9:16) for **TikTok, Instagram Reels and Facebook Reels**. Then an **automatic check** watches it for AI mistakes (hands, faces, things that change between scenes), words not in the script, the name said wrongly and claims beyond your facts; its findings are on the video card. When it says **fix**, the Social Media Manager does not propose it. Otherwise the **Social Media Manager** proposes when and where it goes out — you clear that post as usual.

- A scene the video model declines is **not paid**; the video shows which scene and why. **Try again** makes only the scenes that are missing.
- **Delete video** removes the file once its posts are out (it is refused while a post still waits for it). The posts stay on the networks; the concept stays here for the record (tab **Deleted**).
- Under **Brand**: the **monthly video budget** (empty: no videos are made), the **quality** (Fast about $0.10 per second, Best about $0.40), and **how many videos per day or week** (empty: none are proposed). A 24-second video at Fast costs about $2.40. Concepts waiting for you count towards the number, so you are never offered more than you asked for.

## Results

Followers per network, totals per network (posts, reach, views, likes, comments, shares, saves, clicks) and the best posts of the last 7, 30 or 90 days — read from the networks every hour. The agents read the same numbers to learn what works.

## Connections

Each network lets a service post only through an **app registered in your name** — set up once, then **Connect**. The steps are shown in each card; paste the **redirect address** shown there into the app.

- **Facebook + Instagram** — one Meta app (Business type, "Facebook Login for Business" and "Instagram"). Facebook Login for Business signs in with a **configuration** you make once in the app (Configurations → Create: user access token, Pages and Instagram accounts, the permissions listed in the card); paste its **Configuration ID** into the card — without it Meta answers "Invalid Scopes". In development mode it posts to your own Page and its linked Instagram business account without Meta's review. Page tokens do not expire while you stay admin of the Page.
- **LinkedIn company page** — a LinkedIn app linked to your company page with the **Community Management API** product, which LinkedIn reviews first. The sign-in lasts 60 days and is renewed automatically while LinkedIn allows it.
- **TikTok** — an app at developers.tiktok.com with **Login Kit** and the **Content Posting API** (Direct Post). Until TikTok has reviewed (audited) the app, it may post only **privately**: make each video public in the TikTok app, or submit the app for review. Videos are marked as AI-generated on TikTok. The sign-in lasts a day and is renewed automatically.
- **YouTube** — a Google Cloud project with the YouTube Data API v3 and an OAuth client (web). Until Google verifies the app, uploads stay private; you make them public in YouTube Studio.

App secrets and tokens are stored encrypted and never shown. **Disconnect** deletes the tokens here; remove the app's access at the network as well. **Posted by hand** lists the networks you post on yourself.

## Brand

The **brand kit** the agents and the studio work with: colours (headline band, text, accent), the logo and where it sits, what the company does (**About** — every agent reads it), how it sounds, the picture style, words to use and to avoid, hashtags, the languages besides English, how the name is said, and the AI label. **Budget and rhythm**: the monthly picture budget, the picture quality, the most posts per network and day, the **posting hours** (your time zone) at which approved posts without their own time go out, and the **video** budget, quality and number of videos per day or week.

Changes to the brand kit, the budget, the character, the apps and the connections, and every deleted video, are recorded in the [admin log](docs:admin-log).

## When the check finds mistakes in a video

Every finished video is watched by an automatic check. When it finds mistakes, the video card shows them with their time and offers **Make again from scene N** with its price: only the scene with the first mistake and the ones after it are made again (the scenes before are kept and not paid again), the video model is told what went wrong, and the new video is checked again. Scenes are made one after another, each continuing from the last picture of the one before, so the person, glasses, clothes and objects stay the same across the cuts.
