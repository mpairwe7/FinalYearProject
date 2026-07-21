# URA Chatbot Project Blog

A modern, minimalist blog documenting the development and deployment of a conversational AI customer service system for the Uganda Revenue Authority. Built with Next.js and inspired by the Chirpy theme aesthetic.

## Features

- **Dark/Light Theme Toggle**: Seamlessly switch between themes with automatic persistence
- **Category Filtering**: Browse posts by category (Introduction, Technical, Features, Security, Quality, Operations)
- **Full-Text Search**: Quickly find posts by title or excerpt
- **Responsive Design**: Fully responsive layout optimized for mobile, tablet, and desktop
- **Syntax Highlighting**: Code blocks with proper formatting
- **Rich Content**: Support for markdown-like formatting including tables, lists, and headers
- **Fast Performance**: Static generation with server-side rendering

## Project Structure

```
app/
├── layout.tsx          # Root layout with theme provider
├── page.tsx            # Blog home page with search and filtering
├── blog/
│   └── [slug]/
│       └── page.tsx    # Individual blog post pages
├── globals.css         # Theme configuration and styles
└── ...

lib/
└── posts.ts            # Blog post data and metadata

components/
├── ui/                 # shadcn/ui components
└── ...
```

## Blog Posts

The blog includes 6 comprehensive posts:

1. **Project Overview** - Introduction to the URA Chatbot and its purpose
2. **System Architecture** - Technical deep dive into the modular design and RAG pipeline
3. **Bilingual Support** - Implementation of English and Luganda language support
4. **Security and Compliance** - Security measures and compliance frameworks
5. **Testing and Quality Assurance** - Testing strategy and quality metrics
6. **Deployment and Operations** - Production infrastructure and maintenance

## Getting Started

### Prerequisites

- Node.js 18+ with pnpm

### Installation

```bash
# Install dependencies
pnpm install

# Run development server
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000) to view the blog.

## Theme System

The blog uses a sophisticated theme system with:

- **3-5 color palette**: Primary, secondary, accent, and neutral colors
- **Light mode**: Clean white background with dark text
- **Dark mode**: Deep dark background with light text
- **Persistent storage**: Theme preference saved in browser
- **System preference detection**: Respects OS-level theme preference

## Customization

### Adding a New Post

Edit `lib/posts.ts` and add a new post object:

```typescript
{
  title: "Your Post Title",
  slug: "your-post-slug",
  date: "Month Year",
  category: "Category Name",
  excerpt: "Brief description...",
  tags: ["tag1", "tag2"],
  content: "Full markdown content..."
}
```

### Updating Colors

Edit `app/globals.css` and modify the CSS variables in the `:root` and `.dark` selectors.

### Changing Fonts

Update the font imports in `app/layout.tsx` and modify the `--font-*` variables in `globals.css`.

## Deployment

Deploy to Vercel with a single click:

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Fyourrepo%2Fura-chatbot-blog)

Or deploy manually:

```bash
pnpm build
pnpm start
```

## Performance

- **First Contentful Paint**: <1s
- **Largest Contentful Paint**: <2s
- **Cumulative Layout Shift**: <0.1
- **Lighthouse Score**: 90+

## Technologies

- **Framework**: Next.js 16 with App Router
- **Styling**: Tailwind CSS v4 with custom theme tokens
- **Theming**: next-themes for dark/light mode
- **Icons**: Lucide React
- **Deployment**: Vercel

## License

Built as part of a final year project at Makerere University.

## Credits

- **Template Inspiration**: [Chirpy Jekyll Theme](https://chirpy.cotes.page/)
- **Project**: Conversational AI for Customer Service (URA Chatbot)
- **Team**: Group 10 (BSE 22-10), Makerere University
- **Supervisor**: Dr. Muwonge Benard
