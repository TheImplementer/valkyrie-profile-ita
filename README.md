# Valkyrie Profile in Italiano: blog

A Jekyll blog (minima theme) for updates on the Italian fan translation. The posts are in Italian.

## What the blog is for

The blog should be as educational as possible. Beyond translation progress, posts look at the
challenges Valkyrie Profile poses and explain how the game works: how its data is stored and
compressed, how its fonts and text work, why some text is hard to find or to change, and so on.
They are written for a curious reader who is not an expert, and kept short.

## Writing a post

Add a Markdown file to `_posts/` named `YYYY-MM-DD-title.md`, with front matter like the first post:

```markdown
---
layout: post
title: "Titolo"
date: 2026-11-20
---

Testo dell'articolo.
```

Images go in an `assets/` folder and are linked as `![descrizione]({{ "/assets/nome.png" | relative_url }})`.

## Publishing on GitHub Pages

1. Create a public GitHub repository and push this one to it.
2. In `_config.yml`, set `url` and `baseurl` for that repository (the comments there show both cases).
3. In the repository's Settings > Pages, choose "Deploy from a branch", branch `main`, folder `/ (root)`.

GitHub builds the site itself, so nothing needs to be installed to publish.

## Previewing locally (optional)

Needs Ruby 3.3 with Bundler (with Homebrew: `brew install ruby@3.3`, then put
`/opt/homebrew/opt/ruby@3.3/bin` first in `PATH`). Gems go in `vendor/bundle`, which is not committed:

```bash
bundle config set --local path vendor/bundle
bundle install
bundle exec jekyll serve
```

If `eventmachine` or `openssl` fails to compile with errors about `struct tm` or `timespec`, a
stray `time.h` (for example an old one in `/usr/local/include`) is hiding the system one. A fix that
stays inside this folder is a link to the SDK's `time.h`, searched first when those gems are built:

```bash
mkdir -p vendor/include-fix
ln -sf "$(xcrun --show-sdk-path)/usr/include/time.h" vendor/include-fix/time.h
bundle config set --local build.openssl "--with-cppflags=-I$PWD/vendor/include-fix"
bundle config set --local build.eventmachine "--with-cppflags=-I$PWD/vendor/include-fix"
```

Then open http://localhost:4000.
