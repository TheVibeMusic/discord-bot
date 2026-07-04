# Changelog
## 1.0.0

☆ Ticket system: persistent "Open Ticket" button panel, private per-member ticket channels under a configured category, staff-role ping, close via button or `$close`, plain-text transcripts posted to a log channel.

☆ Two-tier help: `$help` / `$viibr` staff command menu; `/help` member overview (ephemeral, never lists admin commands).

☆ Bulk message cleanup: `$delete <n>` (admin, max 100) with a Yes/Cancel confirmation step, plus `$setlog` deletion log — single deletions logged with author and content, bulk deletes logged as one summary.

☆ Counting game: `$countinghard` / `$countingeasy` channel setup, `$startgame`, correct counts verified with a reaction, double-count
warning-then-reset rule, no chatting without the number, milestone celebrations at 100/200/500/1000/every 1000 with optional custom media
(`$milestone`).

☆ Birthday tracker: `/addmybd`, `/removemybd`, `/calendar` for members; daily announcement (14:00 UTC) with a greeting card image and a rotating
birthday song; community song submissions via `/birthdaysong` (modal, capped, one per member, Suno links fetched as mp3s) with admin moderation (`$bdsongs`, `$removebdsong`); "Send birthday wishes" button with per-member counter.

☆ Resources: `/resources` links embed with admin management (`$addresource`, `$deleteresource`, `$replaceresource`).

☆ Utility: `$ping` health check, `$sync` slash-command re-sync (admin).

☆ Configuration entirely via environment variables. Per-guild settings stored in MySQL (`vibe_` table prefix).
