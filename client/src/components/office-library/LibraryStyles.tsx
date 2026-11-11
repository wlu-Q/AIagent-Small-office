import styled from '@emotion/styled'

export const LibrarySurface = styled.div`
  --library-ink: #20383c;
  --library-muted: #6b7d80;
  --library-accent: #326e72;
  position: relative; height: 100%; min-height: 0; display: flex; flex-direction: column;
  color: var(--library-ink); background: #fbfcfa;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  button, input { font: inherit; }
  button { cursor: pointer; }
  .MuiIconButton-root { color: #6b7d80; }
  button:disabled { cursor: default; opacity: .5; }
  button:focus-visible, a:focus-visible, input:focus-visible, [tabindex]:focus-visible {
    outline: 3px solid #58a0a4; outline-offset: 4px;
  }
  .library-header { padding: 30px 38px 22px; display: flex; align-items: center; gap: 24px; border-bottom: 1px solid #e5ebe7; }
  .library-heading { flex: 1; min-width: 0; }
  h2 { margin: 0; font-size: 26px; font-weight: 700; letter-spacing: -.7px; }
  .library-heading p { margin: 9px 0 0; font-size: 13px; line-height: 1.7; color: var(--library-muted); }
  .library-close { margin: 0 -22px 0 -10px; flex: 0 0 auto; }
  .library-tools { display: flex; align-items: center; flex-wrap: nowrap; gap: 12px; margin-left: auto; }
  .library-search { display: flex; align-items: center; gap: 9px; padding: 0 12px; width: 226px;
    height: 40px; border: 1px solid #dce5e2; border-radius: 8px; color: #7b8c8e; background: #fff; }
  .library-search:focus-within { border-color: #45888a; box-shadow: 0 0 0 3px #45888a12; }
  .library-search input { width: 100%; min-width: 0; border: 0; outline: none; background: transparent; color: var(--library-ink); font-size: 12px; }
  .library-search input:focus-visible { outline: none; }
  .library-search input::placeholder { color: #849293; }
  .library-primary { min-height: 40px; display: inline-flex; gap: 8px; justify-content: center; align-items: center;
    padding: 0 18px; border: 1px solid #2d696d; border-radius: 8px; color: #fff; background: #326e72;
    box-shadow: 0 3px 8px #254f5315; font-size: 13px; transition: background .16s; }
  .library-primary:hover { background: #245b5f; }
  .library-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 28px 38px 12px; scrollbar-width: thin; scrollbar-color: #ced8d4 transparent; }
  .library-item-status { font-size: 11px; color: #6b7d80; margin-top: 7px; overflow-wrap: anywhere; }
  .library-item-actions { display:flex;gap:12px;margin-top:7px; }
  .library-item-actions:empty { display:none; }
  .library-item-actions button { background:none;border:0;padding:2px 0;color:#326e72;font-size:11px; }
  .library-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); column-gap: 24px; row-gap: 26px; }
  .library-book { width: 100%; min-width: 0; border: 0; background: none; padding: 0; text-align: left; color: var(--library-ink); }
  .library-book:focus-visible { outline-offset: 5px; border-radius: 4px; }
  .library-book-space { position: relative; display: flex; align-items: flex-end; justify-content: center; height: 205px; padding-bottom: 8px; perspective: 850px; }
  .library-book-space::after { content: ''; position: absolute; left: -12px; right: -12px; bottom: 0; height: 7px;
    background: linear-gradient(#e6e4dc, #f7f5ef 38%, #d4d2c8); border-bottom: 1px solid #c5c5bc; box-shadow: 0 7px 8px -3px #33423a24; }
  .library-cover { position: relative; width: 86%; height: 193px; max-width: 158px; border-radius: 2px 5px 5px 2px;
    background: var(--book-paper); color: var(--book-ink); overflow: hidden; isolation: isolate;
    box-shadow: 5px 9px 12px -4px #293b3b35, inset -3px 0 0 #ffffff50;
    transform: none; transform-origin: center; transition: transform .18s ease-out, box-shadow .25s; will-change: transform; }
  .library-cover::before { content: ''; position: absolute; z-index: 3; inset: 0 auto 0 0; width: 12px;
    background: linear-gradient(90deg, #00000024, #ffffff55 27%, #0000000b 47%, #00000015 69%, #ffffff45 82%, transparent); }
  .library-cover::after { content: ''; position: absolute; z-index: 2; inset: 0; pointer-events: none; opacity: .25;
    background-image: repeating-linear-gradient(0deg, #ffffff2e 0, #ffffff2e 1px, transparent 1px, transparent 3px),
      repeating-linear-gradient(90deg, #0000000e 0, #0000000e 1px, transparent 1px, transparent 4px); }
  .library-cover-title { position: relative; z-index: 2; display: block; margin: 22px 13px 0 24px; max-width: 5em;
    font: 600 21px/1.4 'Songti SC', SimSun, serif; letter-spacing: 1px; overflow-wrap: anywhere;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .library-cover-subtitle { position: relative; z-index: 2; display: block; margin: 9px 14px 0 24px;
    max-width: 100px; font: 9px/1.5 Georgia, serif; opacity: .66; }
  .library-art { position: absolute; width: 100%; height: 110px; bottom: -1px; left: 0; }
  .library-book:hover .library-cover, .library-book:focus-visible .library-cover {
    transform: translateY(-5px) rotateX(var(--tilt-x, 0deg)) rotateY(var(--tilt-y, 0deg)); box-shadow: 6px 15px 18px -6px #293b3b45;
  }
  .library-book-name { display: block; margin: 11px 0 6px; font-size: 12px; line-height: 1.5; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .library-book-meta { display: flex; align-items: center; gap: 9px; font-size: 10px; color: #7a8c8a; }
  .library-delete { border: 0; border-left: 1px solid #dce5e2; background: none; padding: 3px 6px 3px 10px; color: #8a6460; font-size: 11px; border-radius: 0 4px 4px 0; }
  .library-delete:hover:not(:disabled) { color: #a13e36; background: #f8eeec; }
  .library-format { display: inline-block; border: 1px solid #d8e2e2; border-radius: 4px; padding: 1px 5px;
    color: #476a72; background: #f1f5f5; font: 10px/1.5 'Segoe UI', sans-serif; }
  .library-footer { display: flex; align-items: center; gap: 16px; justify-content: flex-end; flex-wrap: wrap;
    padding: 13px 38px; border-top: 1px solid #e8eeea; background: #f6f9f6; color: #7a8a85; font-size: 11px; line-height: 1.7; }
  .library-footer p { margin: 0; }
  .library-empty { min-height: 320px; height: 100%; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; gap: 14px; }
  .library-empty > svg { font-size: 58px; color: #96b4af; }
  .library-empty h3 { font-size: 18px; font-weight: 500; margin: 0; }
  .library-empty p { color: #7a8c88; margin: 0; max-width: 340px; font-size: 13px; line-height: 1.9; }
  .library-text-button { border: 0; background: transparent; color: #326e72; padding: 8px; font-size: 12px; }
  .library-shelf { height: 100%; min-height: 0; display: flex; flex-direction: column; }
  .library-shelf.is-hidden { visibility: hidden; }
  .library-shelf.is-inactive { pointer-events: none; }
  .library-book.is-opening .library-cover { visibility: hidden; }
  .library-reader { position: absolute; inset: 0; display: flex; flex-direction: column; background: #fbfcfa;
    opacity: 0; pointer-events: none; transition: opacity .18s; }
  .library-reader.is-ready { opacity: 1; pointer-events: auto; }
  .library-reader.is-opening { animation: library-reader-reveal .18s .52s both; }
  @keyframes library-reader-reveal { from { opacity: 0; } to { opacity: 1; } }
  .library-transition { position: absolute; inset: 0; overflow: hidden; pointer-events: all; z-index: 3; perspective: 1600px; }
  .library-flight { position: absolute; top: 0; left: 0; transform-origin: 0 0; perspective: 1400px; }
  .library-flight .library-cover { position: absolute; inset: 0; width: 100%; max-width: none; height: 100%;
    transform-origin: left center; backface-visibility: hidden; visibility: visible; transition: none; }
  .library-flight .library-cover-title { font-size: clamp(26px, 4vw, 46px); margin: 12% 10% 0 13%; max-width: 7em; }
  .library-flight .library-cover-subtitle { margin-left: 13%; font-size: 14px; }
  .library-flight .library-art { height: 55%; }
  .library-leaf { position: absolute; inset: 0; border-radius: 2px 7px 7px 2px; background: #fffefa;
    box-shadow: 0 20px 55px #20383c24, inset 8px 0 12px #20383c0c; padding: 12%; }
  .library-leaf span { display: block; margin-bottom: 12%; font: 26px/1.6 SimSun,serif; }
  .library-leaf i { display: block; height: 1px; margin: 22px 0; background: #d9e1dc; }
  .library-leaf i:last-child { width: 60%; }
  .library-docx { flex: 1; min-height: 0; width: 100%; border: 0; background: #f3f5f3; }
  .library-pdf { background: #edf1ef; padding: 24px; }
  .library-pdf-page { position: relative; width: 100%; max-width: 1000px; aspect-ratio: 210 / 297; margin: 0 auto 28px;
    background: white; box-shadow: 0 2px 14px #20383c12; }
  .library-pdf-page canvas { display: block; width: 100%; height: 100%; }
  .library-page-number { position: absolute; bottom: -23px; left: 0; right: 0; text-align: center; font-size: 11px; color: #73877e; }
  .library-pdf-page p { position: absolute; inset: 30% 20px auto; text-align: center; }
  .library-reader-toolbar { min-height: 70px; padding: 14px 26px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid #e2eae5; }
  .library-reader-toolbar h2 { font-size: 16px; letter-spacing: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; flex: 1; }
  .library-reader-toolbar h2:focus { outline: none; }
  .library-back { border: 0; background: transparent; color: #466c69; display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 8px 10px 8px 0; flex-shrink: 0; }
  .library-reader-layout { display: flex; min-height: 0; flex: 1; }
  .library-reading { flex: 1; min-width: 0; overflow: auto; padding: 40px clamp(24px, 5vw, 72px); background: #fff; }
  .library-reading h3 { margin: 0 0 24px; font: 600 26px/1.5 'Songti SC', SimSun, serif; }
  .library-reading p, .library-reading li { font-size: 14px; line-height: 2; }
  .library-prose { white-space: pre-wrap; line-height: 2; font-size: 14px; overflow-wrap: anywhere; }
  .library-markdown { line-height: 1.9; font-size: 14px; overflow-wrap: anywhere; }
  .library-markdown h1 { font-size: 27px; }
  .library-markdown h2 { font-size: 22px; margin: 24px 0 12px; }
  .library-markdown pre { padding: 16px; overflow: auto; background: #f1f5f3; border-radius: 8px; }
  .library-markdown code { background: #edf3ef; padding: 2px 4px; }
  .library-markdown blockquote { margin-left: 0; padding-left: 18px; border-left: 3px solid #a4bcb0; color: #6a7c72; }
  .library-markdown table { border-collapse: collapse; display: block; overflow-x: auto; }
  .library-markdown td, .library-markdown th { border: 1px solid #dbe5de; padding: 8px 12px; }
  .library-reader-message { margin: auto; max-width: 380px; padding: 36px; color: #6b8078; text-align: center; line-height: 1.9; font-size: 14px; }
  .library-reader-message svg { font-size: 48px; color: #9cb8ab; }
  .library-reader-message h3 { color: #28443b; font-size: 19px; }
  .library-reader-message a { color: #326e72; }
  @media (max-width: 1000px) {
    .library-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .library-header { padding: 25px 28px 20px; gap: 16px; }
    .library-scroll { padding: 24px 28px 12px; }
    .library-heading h2 { font-size: 23px; }
    .library-search { width: 185px; }
  }
  @media (max-width: 720px) {
    .library-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 18px; }
    .library-header { flex-wrap: wrap; position: relative; padding: 24px; }
    .library-heading { flex-basis: 100%; padding-right: 20px; }
    .library-tools { width: 100%; }
    .library-search { flex: 1; }
    .library-close { position: absolute; right: 12px; top: 12px; margin: 0; }
    .library-scroll { padding: 22px 24px; }
    .library-footer { padding: 12px 24px; gap: 2px; }
    .library-reader-toolbar { padding: 12px; gap: 6px; flex-wrap: wrap; }
    .library-reader-toolbar h2 { font-size: 14px; }
  }
  @media (max-width: 480px) {
    .library-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .library-reader-layout { flex-direction: column; }
    .library-reading { padding: 24px; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
    .library-book:hover .library-cover, .library-book:focus-visible .library-cover { transform: none; }
  }
`
