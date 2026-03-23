const fs = require('fs');
let c = fs.readFileSync("f:/Claude Folder/FF Dashboard/main-mockup.html", "utf8");

const newCardCSS = `/* -- Player card wrapper (card body + icon strip below) -- */
.p-card-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0;
  flex-shrink: 0;
  cursor: pointer;
  transition: transform .15s cubic-bezier(.34,1.56,.64,1);
}
.p-card-wrap:hover { transform: translateY(-4px); }
.p-card-wrap:hover .p-card { border-color: rgba(255,255,255,.22); box-shadow: 0 8px 24px rgba(0,0,0,.5); }
.p-card-wrap:active { transform: scale(.96); }

/* Card body */
.p-card {
  width: 130px;
  background: linear-gradient(160deg, rgba(14,26,58,.98) 0%, rgba(8,16,38,.98) 100%);
  border: 1.5px solid rgba(255,255,255,.12);
  border-radius: 12px;
  padding: 14px 10px 12px;
  text-align: center;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
  transition: border-color .15s, box-shadow .15s;
}
.p-card.captain::before {
  content: "\u00A9";
  position: absolute;
  top: -6px; right: -6px;
  width: 20px; height: 20px;
  background: var(--gk);
  border-radius: 50%;
  font-size: 10px;
  font-weight: 900;
  display: flex; align-items: center; justify-content: center;
  color: #000;
  z-index: 3;
  border: 1.5px solid rgba(0,0,0,.4);
}

/* Team logo watermark */
.p-card .p-logo {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: .38;
  pointer-events: none;
  overflow: hidden;
  border-radius: 11px;
}
.p-card .p-logo img { width: 80px; height: 80px; object-fit: contain; filter: grayscale(15%); }

/* Player name */
.p-card .p-name {
  font-size: 13px;
  font-weight: 800;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
  position: relative;
  z-index: 1;
  line-height: 1.3;
  text-shadow: 0 1px 4px rgba(0,0,0,.7);
  letter-spacing: .2px;
}

/* Points -- color rules: green>=5pts, orange>=10pts, blaze>=12pts */
.p-card .p-pts {
  font-family: 'Barlow Condensed', sans-serif;
  font-size: 36px;
  font-weight: 900;
  color: rgba(255,255,255,.4);
  position: relative;
  z-index: 1;
  line-height: 1.0;
  text-shadow: 0 2px 6px rgba(0,0,0,.5);
  letter-spacing: -1px;
}
.p-card .p-pts.good  { color: var(--green-lt); text-shadow: 0 0 12px rgba(76,175,80,.4); }
.p-card .p-pts.hot   { color: var(--fire);     text-shadow: 0 0 16px rgba(255,87,34,.55); }
.p-card .p-pts.blaze { color: #f97316;          text-shadow: 0 0 14px rgba(249,115,22,.5); }

/* Fire tier glow borders */
.p-card.fire-1 {
  border-color: rgba(255,87,34,.35);
  box-shadow: 0 0 10px rgba(255,87,34,.18), inset 0 0 20px rgba(255,87,34,.04);
}
.p-card.fire-2 {
  border-color: rgba(255,87,34,.6);
  box-shadow: 0 0 18px rgba(255,87,34,.28), inset 0 0 30px rgba(255,87,34,.06);
  animation: fire-pulse 2.4s ease-in-out infinite;
}
.p-card.fire-3 {
  border-color: rgba(255,120,30,.75);
  box-shadow: 0 0 28px rgba(255,100,20,.4), 0 0 8px rgba(255,120,30,.25), inset 0 0 40px rgba(255,80,20,.07);
  animation: fire-pulse 1.8s ease-in-out infinite;
}
@keyframes fire-pulse {
  0%, 100% { box-shadow: 0 0 18px rgba(255,87,34,.28), inset 0 0 30px rgba(255,87,34,.06); }
  50%       { box-shadow: 0 0 32px rgba(255,87,34,.48), inset 0 0 40px rgba(255,87,34,.10); }
}

/* Events strip -- rendered below the card body */
.p-events {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  min-height: 18px;
  padding: 2px 0 0;
  flex-wrap: wrap;
}
.p-ev {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  line-height: 1;
}
.p-ev.goal   { background: rgba(76,175,80,.18);  color: #81c784; border: 1px solid rgba(76,175,80,.3); }
.p-ev.assist { background: rgba(66,165,245,.18); color: #64b5f6; border: 1px solid rgba(66,165,245,.3); }
.p-ev.cs     { background: rgba(255,193,7,.15);  color: #ffd54f; border: 1px solid rgba(255,193,7,.25); }
.p-ev.yc     { background: rgba(255,193,7,.18);  color: #ffca28; border: 1px solid rgba(255,193,7,.3); }
.p-ev.rc     { background: rgba(239,83,80,.18);  color: #ef9a9a; border: 1px solid rgba(239,83,80,.3); }`;

// Find and replace the old card CSS block
// Find the start and end markers
const startMarker = '/* Player card -- matches current app (dark navy, large, visible logo) */';
const startMarker2 = '/* Player card \u2014 matches current app (dark navy, large, visible logo) */';

// find by searching for known unique strings at start and end
const startIdx = c.indexOf('/* Player card');
if (startIdx === -1) { console.error('START not found'); process.exit(1); }

// End is at fire-3 closing brace, followed by bottom-section comment
const endMarker = '/* Bottom section: bench + transfers */';
const endIdx = c.indexOf(endMarker);
if (endIdx === -1) { console.error('END not found'); process.exit(1); }

// Replace the whole block between start (inclusive) and end (exclusive)
c = c.slice(0, startIdx) + newCardCSS + '\n\n' + c.slice(endIdx);
console.log('Card CSS replaced. Has p-card-wrap:', c.includes('.p-card-wrap'));

// ── 2. Update JS renderPitch to use .p-card-wrap wrapper and correct pts classes ──
// Replace the card rendering function section
const oldCardRender = `    const cards = row.map(p => {
      const tier = fireTier(p.pts, p.goals, p.assists);
      const ptsCls = p.pts >= 15 ? 'hot' : p.pts >= 10 ? 'blaze' : '';
      let events = '';
      if (p.goals > 0) events += \`<span class="p-ev goal">\${'\\u26BD'.repeat(p.goals)}</span>\`;
      if (p.assists > 0) events += \`<span class="p-ev assist">\${'\\uD83D\\uDC5F'.repeat(p.assists)}</span>\`;
      if (p.cs) events += \`<span class="p-ev cs">\\uD83D\\uDEE1</span>\`;
      return \`<div class="p-card\${p.captain ? ' captain' : ''}\${tier ? ' fire-'+tier : ''}">
        <div class="p-logo"><img src="UCL-Fantasy-Friends-main/static/logos/\${p.teamCode}.png" alt="" onerror="this.style.display='none'"/></div>
        <div class="p-name">\${p.name}</div>
        <div class="p-pts \${ptsCls}">\${p.pts}</div>
        \${events ? \`<div class="p-events">\${events}</div>\` : ''}
      </div>\`;
    }).join('');`;

// Search for the render block with various possible emoji encodings
const renderStart = c.indexOf('    const cards = row.map(p => {');
const renderEnd = c.indexOf("    }).join('');", renderStart) + "    }).join('');".length;

if (renderStart === -1) {
  console.error('Render block not found');
} else {
  const newCardRender = `    const cards = row.map(p => {
      const tier = fireTier(p.pts, p.goals, p.assists);
      const ptsCls = p.pts >= 12 ? 'blaze' : p.pts >= 10 ? 'hot' : p.pts >= 5 ? 'good' : '';
      let evHtml = '';
      for (let g = 0; g < (p.goals||0); g++) evHtml += '<span class="p-ev goal">\\u26BD</span>';
      for (let a = 0; a < (p.assists||0); a++) evHtml += '<span class="p-ev assist">\\uD83D\\uDC5F</span>';
      if (p.cs) evHtml += '<span class="p-ev cs">\\uD83D\\uDEE1</span>';
      return \`<div class="p-card-wrap">
        <div class="p-card\${p.captain ? ' captain' : ''}\${tier ? ' fire-'+tier : ''}">
          <div class="p-logo"><img src="UCL-Fantasy-Friends-main/static/logos/\${p.teamCode}.png" alt="" onerror="this.style.display='none'"/></div>
          <div class="p-name">\${p.name}</div>
          <div class="p-pts \${ptsCls}">\${p.pts}</div>
        </div>
        <div class="p-events">\${evHtml}</div>
      </div>\`;
    }).join('');`;
  c = c.slice(0, renderStart) + newCardRender + c.slice(renderEnd);
  console.log('Render block replaced');
}

fs.writeFileSync("f:/Claude Folder/FF Dashboard/main-mockup.html", c, "utf8");
console.log('All done.');
