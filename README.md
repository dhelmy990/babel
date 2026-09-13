# dhelmy.stream

Run the website locally:

```bash
python3 -m http.server 3000 --bind 127.0.0.1
```

Open http://localhost:3000/ for the timeline. The **Explore the galaxy** button
opens the original Babel graph at `/galaxy/index.html` (also served at `/galaxy/`).

The homepage uses the approved split introduction layout. Its articles, review
list, and draggable notes are currently sample content; publishing, accounts,
private note storage, and review scheduling are not connected yet.

The existing Electron entry point also opens the homepage with `npm start`.
