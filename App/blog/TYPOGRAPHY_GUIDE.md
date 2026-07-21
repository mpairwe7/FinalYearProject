# URA Chatbot Blog - Typography & Design Guide

## Typography Enhancements

This blog now features a comprehensive typography system designed for optimal readability and professional presentation across all content types.

### Heading Hierarchy

All headings follow a structured hierarchy with proper spacing and letter-spacing for visual hierarchy:

- **H1 (2.5rem)**: Page titles with 1.2 line-height and -0.02em letter-spacing
- **H2 (2rem)**: Section headings with 1.3 line-height and -0.01em letter-spacing
- **H3 (1.5rem)**: Subsection headings with 1.4 line-height
- **H4-H6**: Supporting headings with semibold weight

### Body Text

- **Font Size**: 1rem (16px)
- **Line Height**: 1.7 for optimal readability
- **Letter Spacing**: 0.3px for improved clarity
- **Text Color**: Foreground color at 85% opacity for better contrast in light/dark modes

### Lists

- **Spacing**: 0.75rem between items for visual breathing room
- **Padding**: 1.5rem left indent for proper alignment
- **Line Height**: 1.7 for readability

### Code Blocks

- **Background**: Secondary color with 50% opacity
- **Font Family**: Monospace (Geist Mono)
- **Line Height**: 1.5 for code readability
- **Padding**: 1rem (16px) for comfortable spacing

### Tables

- **Header Background**: Secondary color
- **Borders**: Consistent with theme border color
- **Padding**: 0.75rem in cells for breathing room
- **Hover Effect**: Subtle background change on table rows

### Special Elements

- **Strong/Bold**: Semibold weight with full foreground color
- **Italic/Emphasis**: Italic style with 90% foreground opacity
- **Links**: Accent color with underline on hover
- **Blockquotes**: Left accent border with italic styling

## Theme Support

All typography responds to both light and dark themes:

### Light Theme
- Clear contrast for readability
- Foreground text at 85% opacity for body text
- Subtle gray accents for secondary content

### Dark Theme
- Reduced brightness to minimize eye strain
- Adjusted colors for night viewing
- Proper contrast ratios for accessibility

## Responsive Design

Typography scales responsively:
- Headings: Adjusted font sizes on smaller screens
- Body text: Consistent 1rem base size
- Padding/margins: Scale down on mobile devices
- Tables: Horizontal scroll on mobile for accessibility

## Accessibility Features

- **WCAG 2.1 AA Compliant**: All text meets minimum contrast ratios
- **Semantic HTML**: Proper heading structure throughout
- **Line Height**: 1.6-1.7 for dyslexia-friendly reading
- **Letter Spacing**: 0.3px for improved clarity
- **Font Smoothing**: Applied antialiasing for better rendering

## Blog Post Structure

### Meeting the Team Post
- **Title**: H1 with 5xl size on desktop
- **Metadata**: Small text with accent badges for categories
- **Body**: 1rem paragraphs with 1.7 line-height
- **Team Member Headings**: H3 with bold styling
- **Contributions**: Bulleted lists with proper spacing

### Contributor Deep Dives
- **Technical Content**: Code blocks with syntax highlighting
- **Tables**: Technology stacks and metrics with proper borders
- **Lists**: Organized with consistent spacing
- **Emphasis**: Bold terms for technical concepts

## Color System

### Accent Color
- Light Mode: Vibrant blue (#3B82F6 equivalent in oklch)
- Dark Mode: Same vibrant blue for consistency
- Used for: Links, badges, highlights, category tags

### Neutral Colors
- Primary: Deep gray/near-black for headings
- Secondary: Light gray for backgrounds and subtle elements
- Muted: Medium gray for secondary text

## Font Stack

```css
--font-sans: 'Geist', 'Geist Fallback';
--font-mono: 'Geist Mono', 'Geist Mono Fallback';
```

- **Primary Font**: Geist (clean, modern sans-serif)
- **Monospace Font**: Geist Mono (code blocks)
- **Fallback**: System fonts for reliability

## Implementation Details

### CSS Custom Properties
All typography uses CSS custom properties defined in `globals.css`:
- Semantic naming: `--font-sans`, `--font-mono`
- Theme-aware: Different values for light/dark modes
- Responsive: Scales on different breakpoints

### Tailwind Classes
Typography classes build on Tailwind's utility system:
- Font sizing: `text-xl`, `text-base`, `text-sm`
- Font weight: `font-bold`, `font-semibold`, `font-medium`
- Line height: `leading-relaxed`, `leading-snug`, `leading-tight`
- Letter spacing: Applied via base layer styles

## Usage Examples

### Blog Post Header
```html
<h1>Main Title Here</h1>
<p class="text-lg text-muted-foreground">Subtitle or excerpt</p>
```

### Section with Lists
```html
<h2>Section Title</h2>
<ul class="list-disc space-y-3 pl-6">
  <li>Item with proper spacing</li>
  <li>Another list item</li>
</ul>
```

### Code Block
```html
<pre>
  <code>code content here</code>
</pre>
```

### Table
```html
<table>
  <thead><tr><th>Header</th></tr></thead>
  <tbody><tr><td>Data</td></tr></tbody>
</table>
```

## Testing Recommendations

- Test all content in both light and dark modes
- Verify readability at different screen sizes
- Check accessibility with screen readers
- Validate contrast ratios with WCAG tools
- Review on various devices (mobile, tablet, desktop)

## Future Improvements

- Add custom font weights for even better typography control
- Implement better code syntax highlighting
- Add support for quote styling
- Consider implementing typographic scale tool
- Add print-friendly typography styles
