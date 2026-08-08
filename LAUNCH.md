# Материалы запуска shipproof

Всё на английском — рынок глобальный. Ниже: посты под конкретные площадки,
правила поведения и что считать успехом.

---

## ГЛАВНОЕ ПРАВИЛО

Мы заходим **не с рекламой, а с историей поломки**. Проверено: из 49
сабреддитов, где фаундеры продвигаются, **39% банят саморекламу полностью**,
ещё **22% пускают только по правилу 9:1** (на 1 пост о себе — 9 обычных
полезных ответов), и **ровно один** приветствует открыто.

История «у меня прод молча откатывался 13 раз» — это польза, а не промо.
Ссылка на инструмент идёт **в конце и один раз**, либо в комментарии, если
правила саба запрещают ссылки в теле.

Три вещи, за которые банят мгновенно:
1. промо с аккаунта младше 7 дней;
2. сокращатели ссылок (bit.ly и подобные) — только прямые ссылки;
3. больше 8 постов в сутки.

---

## ПОСТ 1 — r/devops, r/programming, r/webdev

Заголовок:

> My deploy tool said "success" 13 times while the old build was still serving

Текст:

```
Small team (me). For months every deploy ended the same way: command exits 0,
tooling says "deployed successfully", I move on. Then a user messages me that
the site is showing the old version, or an error page.

Thirteen times before I stopped blaming luck.

The gap is that "the deploy command finished" and "users are getting the new
build" are different statements. Three things can break between them and none
of them produce an error you can see:

- The deploy never ran. Health check returns 200 — served by the OLD build,
  which is perfectly healthy.
- The new build started, passed the platform health check, crashed on the
  first real request, platform restored the previous version. 40 seconds
  total. Your tooling finished reporting success at second 12.
- It flapped between old and new while the orchestrator made up its mind.

Uptime monitors don't help — they answer "is the server up", and the answer
is yes, cheerfully, while serving code from last Tuesday.

So I wrote ~40 lines that fingerprint the live URL before the deploy and wait
for it to change after. Caught the next silent rollback the same week.

The bug in my own first version taught me more than the tool did: when a
deploy rolled back, it reported "the response never changed". Technically
true at the moment you look, and completely misleading — the deploy DID run,
went live, and died. Those need different responses from you.

Cleaned it up: https://github.com/Hurshid87/shipproof (MIT, zero deps)

Genuinely curious which failure modes I still haven't hit.
```

---

## ПОСТ 2 — r/AI_Agents, r/vibecoding, r/ClaudeAI

Заголовок:

> Your coding agent cannot tell whether a deploy landed. It reports success on exit code 0.

Текст:

```
An agent's entire universe is the exit code of a subprocess. When it writes
"deployed successfully", the literal meaning is "a command returned 0".

That's compatible with all of these:
- deploy never ran, old build still serving (health check returns 200 — from
  the old build)
- new build booted, crashed on first real traffic, platform rolled back
- it flapped between versions for a minute

The agent isn't lying on purpose. It has no way to know.

I got bitten 13 times on my own production site before writing a check the
agent doesn't control: fingerprint the URL before deploying, then wait until
you can prove (a) the response actually changed, (b) it came back if it went
down, (c) it held for a settle window without flapping back.

Exit non-zero if you can't prove it. Then put this in CLAUDE.md / AGENTS.md:

    ## Deploys
    Never report a deploy as done because a command exited 0.
    Run `shipproof verify <url>`. Non-zero means it did NOT land.

The agent still can't see whether the deploy landed. It just can't claim it
did anymore.

https://github.com/Hurshid87/shipproof — MIT, no dependencies.
```

---

## ПОСТ 3 — r/SideProject, r/SaaS, r/indiehackers

Заголовок:

> Built a tool because my own site lied to me 13 times

Текст: короткая версия истории (первые два абзаца ПОСТА 1) + ссылка.
Эти сабы терпимы к запускам, но там ценят краткость и честные цифры.

---

## КОММЕНТАРИИ (работают лучше постов)

Ищем свежие темы, где человек описывает ровно эту боль. Поисковые запросы:

- `site:reddit.com "deployed" "old version" still showing`
- `site:reddit.com claude code "said it was done" but`
- `site:reddit.com vercel OR render rollback silent`
- в r/devops, r/AI_Agents: сортировка New, ключевые слова
  `rollback`, `still serving old`, `agent said done`

Структура комментария — **без ссылки**:

```
Had exactly this. Turned out the platform restored the previous build after
the new one crashed on first traffic, so the health check was green the whole
time — it was answering from the old version.

What fixed it for me was checking that the RESPONSE changed, not that the
server responds. Fingerprint the URL before deploying, then wait until you
see a different build id AND it holds for ~30s without flapping back.

Happy to share the script if useful.
```

Ссылку даём **только если попросят**. Это и есть правило 9:1 на практике.

---

## ЧТО СЧИТАЕМ РЕЗУЛЬТАТОМ

Замеряем через 14 дней после публикации:

| Итог | Что это значит |
|---|---|
| < 20 установок | боль была только наша — закрываем, потеряна неделя |
| 20–100 | ниша есть, но узкая — держим как визитку, не развиваем |
| > 100 | боль общая — идём дальше: память деплоев, командный отчёт |

Считаем **установки** (PyPI downloads, звёзды на GitHub), а не лайки постов.
Лайк ничего не стоит, установка стоит минуты чужого времени.

---

## ПОРЯДОК ДЕЙСТВИЙ

1. Создать репозиторий `shipproof` на GitHub, залить содержимое папки.
2. Опубликовать на PyPI (`shipproof` — имя свободно, проверено).
3. Опубликовать ARTICLE.md как пост в блоге / на dev.to.
4. Reddit: начать с **комментариев** в r/devops и r/AI_Agents. Посты — не
   раньше чем через 3–4 дня участия, и не более одного в сутки.
5. Через 14 дней — замер по таблице выше.
