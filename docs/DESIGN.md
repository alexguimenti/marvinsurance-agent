# Design System: Warm Minimal (inspired by weaveos.com)

A calm, editorial, product-focused design system. Warm off-white surfaces, deep ink and maroon text, one vivid orange accent, soft warm shadows, and tight, confident typography set in Geist.

Values below were extracted from the live site's computed styles and normalized into tokens.

---

## 1. Design Principles

1. **Warm neutrals, not gray.** Every neutral has a slight brown/red undertone. Avoid pure `#000`, pure gray, and cold blues.
2. **One loud color.** Orange (`--accent`) is used sparingly: primary actions inside product UI, send buttons, highlights, charts. Never for large backgrounds.
3. **Big, tight headlines.** Large sizes, medium weight (500), negative letter spacing.
4. **Quiet UI chrome.** Small 13 to 14px controls, 8px radius, almost no borders. Separation comes from subtle background shifts and soft shadows.
5. **Product as hero.** Real UI mockups sit inside large rounded "window" cards with diffuse shadows.

---

## 2. Color Tokens

### 2.1 Neutrals (light theme)

| Token | Hex | Usage |
|---|---|---|
| `--bg-page` | `#F5F3F2` | Page background |
| `--bg-surface` | `#FBF9F9` | Cards, product window frames |
| `--bg-elevated` | `#FFFFFF` | Inputs, secondary buttons, popovers |
| `--bg-subtle` | `#EEECEA` | Hover rows, segmented controls |
| `--bg-muted` | `#E8E6E1` | Tags, track backgrounds |
| `--bg-chip` | `rgba(255, 252, 249, 0.92)` | Floating chips / pills |
| `--bg-overlay` | `rgba(0, 0, 0, 0.05)` | Hover overlay on any surface |
| `--border-subtle` | `rgba(60, 50, 40, 0.08)` | Default 1px border |
| `--border-strong` | `#D6D1CD` | Dividers, input borders |
| `--border-avatar` | `#BCB6B1` | Avatar rings |

### 2.2 Text

| Token | Hex | Usage |
|---|---|---|
| `--text-heading` | `#460914` | H1 / H2 (deep maroon) |
| `--text-ink` | `#1C1513` | Nav, buttons, strong labels |
| `--text-body` | `#2A2A2A` | Body copy |
| `--text-title-sm` | `#4A4038` | H3 / card titles |
| `--text-secondary` | `#7E746D` | Supporting paragraphs |
| `--text-tertiary` | `#64605D` | Meta info |
| `--text-muted` | `#96918C` | Placeholders, captions, disabled |

### 2.3 Inverse (dark sections, footer, primary button)

| Token | Value | Usage |
|---|---|---|
| `--bg-inverse` | `#1C1513` | Footer, dark bands, primary button |
| `--text-inverse` | `#F3F1EB` | Text on dark |
| `--text-inverse-secondary` | `rgba(243, 241, 235, 0.72)` | Footer links |
| `--text-inverse-muted` | `rgba(243, 241, 235, 0.5)` | Captions on dark |

### 2.4 Brand and accents

| Token | Hex | Usage |
|---|---|---|
| `--accent` | `#FB4F1F` | Primary accent (send button, key highlights) |
| `--accent-strong` | `#B93C22` | Accent hover / pressed |
| `--accent-deep` | `#54251F` | Accent text on light tint |
| `--accent-tint` | `#FEF2EA` | Accent backgrounds, chart fills |
| `--success` | `#B1D68C` | Positive data, status |
| `--info-bg` | `#E0DFFE` | Lavender badge / tertiary button bg |
| `--info-fg` | `#380A8C` | Text on `--info-bg` |

Window "traffic light" dots for mockups: `#FF5F57`, `#FEBC2E`, `#28C840`.

---

## 3. Typography

**Primary font:** `Geist, -apple-system, BlinkMacSystemFont, Arial, sans-serif`
**Mono font:** `ui-monospace, "SF Mono", Menlo, Consolas, monospace` (data, code, numbers in UI mockups)

Geist is free: https://vercel.com/font

### 3.1 Type scale

| Token | Size / Line height | Weight | Letter spacing | Color | Usage |
|---|---|---|---|---|---|
| `display` | 64px / 64px (1.0) | 500 | -0.035em (-2.24px) | `--text-heading` | Hero H1 |
| `h2` | 48px / 52.8px (1.1) | 500 | -0.04em (-1.92px) | `--text-heading` | Section titles, quotes |
| `h3` | 24px / 30px (1.25) | 500 | -0.02em (-0.48px) | `--text-title-sm` | Card / feature titles |
| `h4` | 16px / 20px | 500 | 0 | `--text-ink` | Small titles, footer links |
| `body` | 16px / 24px (1.5) | 400 | 0 | `--text-body` | Paragraphs |
| `body-secondary` | 16px / 24px | 400 | 0 | `--text-secondary` | Supporting copy |
| `ui` | 14px / 21px | 400 | 0 | `--text-ink` | Nav, buttons |
| `ui-sm` | 13px / 18px | 500 | 0 | `--text-ink` | Chips, tabs, small buttons |
| `caption` | 12px / 16px | 400 | 0 | `--text-muted` | Helper text |
| `micro` | 11px / 14px | 500 | 0 | `--text-muted` | Labels inside product UI |

### 3.2 Rules

- Weights used: **400** (body) and **500** (headings, labels). Use 600 only for rare emphasis. Avoid 700+.
- Headlines get negative tracking; body and UI text stay at `normal`.
- Keep headlines short; break lines manually for rhythm.
- Small eyebrow labels (e.g. "Customer Story") use `ui` or `body` size in `--text-secondary`, sentence case, no uppercase.

---

## 4. Spacing and Layout

### 4.1 Spacing scale (4px base)

`4, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128`

```css
--space-1: 4px;  --space-2: 8px;  --space-3: 12px; --space-4: 16px;
--space-5: 20px; --space-6: 24px; --space-8: 32px; --space-10: 40px;
--space-12: 48px; --space-16: 64px; --space-24: 96px; --space-32: 128px;
```

### 4.2 Containers

| Token | Value | Usage |
|---|---|---|
| `--container-max` | `1400px` | Main content width |
| `--container-text` | `620px` | Long text blocks |
| `--container-narrow` | `848px` | Centered product demos |
| Side gutter | `24px` desktop, `16px` mobile | |

- Section vertical padding: `96px` to `128px` desktop, `64px` mobile.
- Generous whitespace; one idea per section.
- Common layout: headline left, visual right (2 columns), stacking on mobile.

---

## 5. Radius

| Token | Value | Usage |
|---|---|---|
| `--radius-xs` | `3px` | Tiny tags, bars in charts |
| `--radius-sm` | `4px` | Small inner elements |
| `--radius-md` | `8px` | **Default**: buttons, inputs, chips, menus |
| `--radius-lg` | `20px` | Inner panels |
| `--radius-xl` | `24px` | Large cards, section containers |
| `--radius-2xl` | `32px` | Hero visual frames |
| `--radius-full` | `999px` | Pills, avatars, toggles |

---

## 6. Elevation (warm shadows)

Shadows use a warm brown base (`rgb(63, 39, 28)`), never pure black.

```css
--shadow-xs: 0 1px 2px rgba(60, 50, 40, 0.06);
--shadow-md: 0 20px 25px rgba(24, 31, 27, 0.06);
--shadow-lg: 0 10px 40px rgba(63, 39, 28, 0.08);   /* product window cards */
--shadow-top: 0 -6px 24px rgba(63, 39, 28, 0.10);  /* bottom sheets, sticky bars */
--shadow-hover: 0 20px 25px rgba(24, 31, 27, 0.15);
```

---

## 7. Components

### 7.1 Buttons

All buttons: `font-size 14px`, `font-weight 400`, `border-radius 8px`, no border, `transition: all 150ms ease`.

| Variant | Background | Text | Notes |
|---|---|---|---|
| **Primary** | `--bg-inverse` `#1C1513` | `#FFFFFF` | Main CTA ("Book a demo") |
| **Secondary** | `#FFFFFF` | `--text-ink` | Paired with primary; add `--shadow-xs` |
| **Tertiary / soft** | `--info-bg` `#E0DFFE` | `--info-fg` `#380A8C` | Alternate highlighted action |
| **Ghost / tab** | `--bg-page` `#F5F3F2` | `--text-ink` | 13px, weight 500, used in tab lists |
| **Accent icon** | `--accent` `#FB4F1F` | `#FFFFFF` | Square icon button (send / submit) |

Sizes:

| Size | Height | Padding |
|---|---|---|
| `sm` (nav) | 32px | `0 10px` |
| `md` (tabs) | 40px | `8px 10px 8px 14px` |
| `lg` (CTA) | 44px | `10px 16px` |

Hover: darken primary slightly (mix 8% white) or apply `--bg-overlay` on light buttons.

### 7.2 Navigation bar

- `position: sticky; top: 0`, height `58px`.
- Floating container inside the page: `--bg-elevated` with slight transparency, `border-radius 8px`, `--shadow-xs`.
- Left: logo. Center-left: 14px links in `--text-ink` with small chevrons for dropdowns. Right: GitHub stars (muted), "Sign in" text link, primary `sm` button.

### 7.3 Cards

**Product window card**
```css
background: var(--bg-surface);
border-radius: 21px to 24px;
box-shadow: var(--shadow-lg);
overflow: hidden;
```
Optional top bar with the three traffic-light dots.

**Feature card**
```css
background: var(--bg-elevated);
border: 1px solid var(--border-subtle);
border-radius: var(--radius-xl);
padding: 24px to 32px;
```

### 7.4 Chips / pills

```css
background: var(--bg-chip);
border: 1px solid var(--border-subtle);
border-radius: 8px;          /* or 999px for pill style */
padding: 4px 10px;
font-size: 13px;
color: var(--text-tertiary);
```

### 7.5 Input (AI prompt box style)

- Container: `--bg-elevated`, `border-radius 8px` to `12px`, `--shadow-xs`, `1px` `--border-subtle`.
- Placeholder: `--text-muted`, 14px.
- Bottom toolbar row with small muted icons (16px) and an accent icon button on the right.
- Below the input: small option chips (`micro` size) and suggestion rows with a `↗` arrow icon in `--text-muted`.

### 7.6 Avatars

- Circle, `border-radius 50%`, background `#F0EBE8`, `1px` border `--border-avatar`.

### 7.7 Testimonial / quote block

- Small eyebrow ("Customer Story") in `--text-secondary`.
- Quote in `h2` style, `--text-heading`, with typographic curly quotes.
- Paired with a data visual on the right (line chart with `--accent` stroke and `--accent-tint` gradient fill).

### 7.8 Footer

- Background `--bg-inverse`, text `--text-inverse`.
- Links: 16px, weight 500, `--text-inverse-secondary`, hover to `--text-inverse`.
- Top corners rounded `24px 24px 0 0` so it reads as a sheet sliding up.

---

## 8. Data Visualization

- Primary series: `--accent` `#FB4F1F`; fill with gradient from `rgba(251,79,31,0.25)` to transparent.
- Baseline / comparison: `--text-muted` dashed line.
- Positive: `--success` `#B1D68C`.
- Gridlines: `--border-subtle`. Labels: `micro` in mono font.

---

## 9. Iconography

- Thin line icons, 1.5px stroke, 16px default (20px in nav dropdowns).
- Color inherits text color; muted icons use `--text-muted`.
- Suggested set: Lucide or Phosphor (Light).

---

## 10. Motion

- Default transition: `150ms ease` for color, background and shadow.
- Cards on hover: shadow from `--shadow-md` to `--shadow-hover`, optional `translateY(-2px)`, `200ms ease-out`.
- Section reveals: fade + 8px rise, `400ms`, staggered `60ms`.
- Respect `prefers-reduced-motion`.

---

## 11. Ready-to-use CSS

```css
:root {
  /* Surfaces */
  --bg-page: #F5F3F2;
  --bg-surface: #FBF9F9;
  --bg-elevated: #FFFFFF;
  --bg-subtle: #EEECEA;
  --bg-muted: #E8E6E1;
  --bg-chip: rgba(255, 252, 249, 0.92);
  --bg-overlay: rgba(0, 0, 0, 0.05);
  --bg-inverse: #1C1513;

  /* Borders */
  --border-subtle: rgba(60, 50, 40, 0.08);
  --border-strong: #D6D1CD;
  --border-avatar: #BCB6B1;

  /* Text */
  --text-heading: #460914;
  --text-ink: #1C1513;
  --text-body: #2A2A2A;
  --text-title-sm: #4A4038;
  --text-secondary: #7E746D;
  --text-tertiary: #64605D;
  --text-muted: #96918C;
  --text-inverse: #F3F1EB;
  --text-inverse-secondary: rgba(243, 241, 235, 0.72);
  --text-inverse-muted: rgba(243, 241, 235, 0.5);

  /* Brand */
  --accent: #FB4F1F;
  --accent-strong: #B93C22;
  --accent-deep: #54251F;
  --accent-tint: #FEF2EA;
  --success: #B1D68C;
  --info-bg: #E0DFFE;
  --info-fg: #380A8C;

  /* Type */
  --font-sans: "Geist", -apple-system, BlinkMacSystemFont, Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;

  /* Radius */
  --radius-xs: 3px;
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 20px;
  --radius-xl: 24px;
  --radius-2xl: 32px;
  --radius-full: 999px;

  /* Shadows */
  --shadow-xs: 0 1px 2px rgba(60, 50, 40, 0.06);
  --shadow-md: 0 20px 25px rgba(24, 31, 27, 0.06);
  --shadow-lg: 0 10px 40px rgba(63, 39, 28, 0.08);
  --shadow-top: 0 -6px 24px rgba(63, 39, 28, 0.10);
  --shadow-hover: 0 20px 25px rgba(24, 31, 27, 0.15);

  /* Layout */
  --container-max: 1400px;
  --container-text: 620px;
}

body {
  margin: 0;
  background: var(--bg-page);
  color: var(--text-body);
  font: 400 16px/1.5 var(--font-sans);
  -webkit-font-smoothing: antialiased;
}

h1, .display { font-size: 64px; line-height: 1; font-weight: 500; letter-spacing: -0.035em; color: var(--text-heading); }
h2 { font-size: 48px; line-height: 1.1; font-weight: 500; letter-spacing: -0.04em; color: var(--text-heading); }
h3 { font-size: 24px; line-height: 1.25; font-weight: 500; letter-spacing: -0.02em; color: var(--text-title-sm); }

@media (max-width: 768px) {
  h1, .display { font-size: 40px; }
  h2 { font-size: 32px; }
  h3 { font-size: 20px; }
}

.btn {
  display: inline-flex; align-items: center; gap: 6px;
  height: 44px; padding: 10px 16px;
  border: 0; border-radius: var(--radius-md);
  font: 400 14px/1 var(--font-sans);
  cursor: pointer; transition: all 150ms ease;
}
.btn-primary   { background: var(--bg-inverse); color: #fff; }
.btn-secondary { background: var(--bg-elevated); color: var(--text-ink); box-shadow: var(--shadow-xs); }
.btn-soft      { background: var(--info-bg); color: var(--info-fg); }
.btn-accent    { background: var(--accent); color: #fff; }
.btn-accent:hover { background: var(--accent-strong); }
.btn-sm { height: 32px; padding: 0 10px; }

.card {
  background: var(--bg-surface);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px;
  background: var(--bg-chip);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  font-size: 13px; color: var(--text-tertiary);
}
```

---

## 12. Tailwind config (optional)

```js
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        page: '#F5F3F2', surface: '#FBF9F9', subtle: '#EEECEA', muted: '#E8E6E1',
        ink: '#1C1513', body: '#2A2A2A', heading: '#460914',
        secondary: '#7E746D', tertiary: '#64605D', faint: '#96918C',
        inverse: '#F3F1EB',
        accent: { DEFAULT: '#FB4F1F', strong: '#B93C22', deep: '#54251F', tint: '#FEF2EA' },
        success: '#B1D68C',
        info: { bg: '#E0DFFE', fg: '#380A8C' },
      },
      fontFamily: {
        sans: ['Geist', '-apple-system', 'BlinkMacSystemFont', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SF Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        display: ['64px', { lineHeight: '64px', letterSpacing: '-0.035em', fontWeight: '500' }],
        h2: ['48px', { lineHeight: '52.8px', letterSpacing: '-0.04em', fontWeight: '500' }],
        h3: ['24px', { lineHeight: '30px', letterSpacing: '-0.02em', fontWeight: '500' }],
      },
      borderRadius: { md: '8px', lg: '20px', xl: '24px', '2xl': '32px' },
      boxShadow: {
        xs: '0 1px 2px rgba(60,50,40,0.06)',
        md: '0 20px 25px rgba(24,31,27,0.06)',
        lg: '0 10px 40px rgba(63,39,28,0.08)',
        top: '0 -6px 24px rgba(63,39,28,0.10)',
      },
      maxWidth: { container: '1400px', text: '620px' },
    },
  },
};
```

---

## 13. Do and Don't

**Do**
- Use `--bg-page` as the base and lift content with `--bg-surface` / white + soft shadow.
- Keep the orange accent to under 5% of any screen.
- Use maroon (`--text-heading`) only for large headlines.
- Show real product UI inside rounded window cards.

**Don't**
- Don't use pure black text or cold gray backgrounds.
- Don't use bold (700) headlines or uppercase headings.
- Don't add heavy borders; prefer 1px `--border-subtle` or no border.
- Don't use the brand logo, mascot, or copy from the reference site; this system covers visual style only.