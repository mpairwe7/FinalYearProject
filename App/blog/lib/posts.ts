export interface BlogPost {
  title: string;
  slug: string;
  date: string;
  category: string;
  excerpt: string;
  content: string;
  tags: string[];
}

export const posts: BlogPost[] = [
  {
    title: 'Meet the Team: URA Chatbot Development Team',
    slug: 'meet-the-team',
    date: 'June 2026',
    category: 'Team',
    excerpt:
      'Meet the talented developers, researchers, and engineers who built the URA Chatbot for their final year project.',
    tags: ['Team', 'Contributors', 'Developers'],
    content: `# Meet the Team: URA Chatbot Development Team

## Project Collaborators

The URA Chatbot is the final-year project of four computer science students at Makerere University. Each member owned core parts of the system, and the four collaborated closely on the documentation and data foundations that everything else was built on. Their photos, individual contribution roles, the efforts they shared, and the full project-flow ownership are shown above.

## How We Worked Together

### Division of Labor
- **System Architecture, ML Processing & Fine-tuning**: Mpairwe Lauben designed the overall architecture and owned the machine-learning core.
- **Backend Development & TTS Pipeline**: Olwol Philly built the FastAPI backend and the text-to-speech pipeline.
- **Frontend Design & Ingestion Pipeline**: Rwemera David built the web experience and the data ingestion pipeline.
- **Optimization, STT Pipeline & Security**: Okwel Edgar Mark optimized performance, built speech-to-text, and implemented security.

### Combined Efforts
All four members worked together on the foundations of the project:
- **SDD Writing**: Authoring the software design document
- **Report Modelling**: Structuring the final project report
- **Database Modelling**: Designing the data schema and relationships
- **Data Collection**: Gathering URA tax content and query data
- **Data Design**: Shaping how data is organized for retrieval

### Collaboration Tools
- **GitHub**: Version control and code review
- **GitHub Issues**: Task management and bug tracking
- **GitHub Projects**: Sprint planning and progress tracking
- **GitHub Actions**: Automated testing and deployment

### Development Process
1. **Sprint Planning**: Bi-weekly sprints with clear goals
2. **Code Review**: Every pull request reviewed by at least 2 team members
3. **Pair Programming**: Complex features developed collaboratively
4. **Testing**: Unit, integration, and end-to-end testing by all team members
5. **Documentation**: Each feature documented by its developer

## Project Stats

- **Total Commits**: 1000+ commits to main repository
- **Lines of Code**: 50,000+ lines across all components
- **Test Coverage**: 85% of codebase covered by automated tests
- **Documentation**: 100+ pages of technical documentation
- **Security Scans**: 10+ different security tools integrated into CI/CD
- **Languages**: Python, TypeScript, JavaScript, SQL
- **Duration**: 12 months from concept to production

## Learning Outcomes

Through this final year project, the team:
- Mastered full-stack development across web, backend, and ML
- Learned production-grade software engineering practices
- Implemented advanced AI/ML concepts in real-world application
- Gained experience with DevOps and infrastructure management
- Understood security and compliance in detail
- Worked effectively as a collaborative team

## Acknowledgments

We're grateful to:
- **Makerere University**: Providing academic support and infrastructure
- **Uganda Revenue Authority**: For partnering on this impactful project
- **Open Source Community**: For libraries and tools that made this possible
- **Our Mentors**: For guidance throughout the project

This project demonstrates what's possible when talented developers collaborate with clear vision and purpose.`,
  },
  {
    title: 'Project Overview: Building Conversational AI for URA',
    slug: 'project-overview',
    date: 'June 2026',
    category: 'Introduction',
    excerpt:
      'Discover how we built a conversational AI system to revolutionize customer service at the Uganda Revenue Authority, enabling 24/7 tax assistance in English and Luganda.',
    tags: ['AI', 'Customer Service', 'Uganda'],
    content: `# Project Overview: Building Conversational AI for URA

## The Challenge

The Uganda Revenue Authority faced a critical challenge: taxpayers were waiting too long for answers to routine tax questions. Support was unavailable outside working hours, human agents were overwhelmed with repetitive queries, and Luganda-speaking taxpayers had no adequate channel for assistance in their preferred language.

## Our Solution

We designed and deployed the **URA Chatbot** — a conversational AI system that allows taxpayers to ask tax-related questions at any time, in either English or Luganda, through text or voice interfaces.

### Key Features

- **24/7 Availability**: Taxpayers can get help anytime, without waiting for business hours
- **Bilingual Support**: Seamless English and Luganda language switching
- **Accuracy Grounding**: Every answer is accompanied by the official URA document it came from
- **Source Verification**: Users can verify information directly from the source
- **Smart Escalation**: When unsure, the system automatically refers cases to human agents
- **Multi-Modal Interface**: Text or voice input, web or mobile access

## Impact

By automating responses to common tax questions, we've dramatically reduced the burden on human agents while ensuring taxpayers get accurate, immediate assistance. This represents a significant step forward in making public services more accessible to all Ugandans.

## Technology

Built with modern software engineering practices, the system prioritizes security, data privacy, and reliability. It's deployed on Makerere University's Crane Cloud infrastructure and requires no installation — just open a web browser.`,
  },
  {
    title: 'System Architecture: Building a Scalable Multilingual Platform',
    slug: 'system-architecture',
    date: 'June 2026',
    category: 'Technical',
    excerpt:
      'Explore the modular architecture behind the URA Chatbot, from retrieval-augmented generation pipelines to CI/CD workflows.',
    tags: ['Architecture', 'RAG', 'MLOps'],
    content: `# System Architecture: Building a Scalable Multilingual Platform

## Modular Design

The URA Chatbot uses a **modular layered architecture** that separates concerns and enables independent scaling of components:

### Core Components

1. **Frontend Layer**: Web and mobile interfaces built for accessibility and responsiveness
2. **API Layer**: RESTful APIs handling user requests and orchestrating backend services
3. **Processing Layer**: Multilingual text processing, translation, and language detection
4. **Retrieval Layer**: Document search and vector-based semantic retrieval
5. **Generation Layer**: Large language models generating contextually accurate responses
6. **Integration Layer**: Connections to external services and legacy systems

## Retrieval-Augmented Generation Pipeline

Our 10-stage RAG pipeline ensures accuracy and source grounding:

1. **Input Processing**: Normalize and clean user queries
2. **Language Detection**: Identify if input is English or Luganda
3. **Translation**: Convert Luganda to English for processing
4. **Query Expansion**: Enhance queries for better retrieval
5. **Hybrid Retrieval**: Combine BM25 keyword search with vector similarity
6. **Reciprocal Rank Fusion**: Merge multiple retrieval results
7. **Document Ranking**: Score documents by relevance
8. **Context Assembly**: Build coherent context from top documents
9. **Generation**: LLM generates response from context
10. **Output Processing**: Format response with sources and translate back to requested language

## Technology Stack

| Component | Technology |
|-----------|------------|
| Frontend | React, TypeScript, Tailwind CSS |
| Mobile | React Native/PWA |
| Backend | Python, FastAPI |
| Database | PostgreSQL, Vector DB |
| LLM | Qwen3-8B with LoRA fine-tuning |
| Deployment | Docker, Kubernetes, Crane Cloud |
| CI/CD | GitHub Actions, Automated Testing |

## CI/CD and MLOps Pipeline

We implemented an end-to-end pipeline for continuous improvement:

- **Automated Testing**: Unit, integration, and end-to-end tests on every commit
- **Model Validation**: Continuous evaluation of retrieval and generation quality
- **Staged Deployment**: Dev → Staging → Production with automated rollbacks
- **Monitoring**: Real-time performance tracking and anomaly detection
- **Feedback Loop**: Production errors feed back into model improvements

This architecture enables us to scale horizontally, handle traffic spikes, and continuously improve the system based on real user feedback.`,
  },
  {
    title: 'Bilingual Support: English and Luganda Integration',
    slug: 'bilingual-support',
    date: 'June 2026',
    category: 'Features',
    excerpt:
      'How we implemented seamless bilingual support for English and Luganda, enabling tax assistance in Uganda\'s most spoken languages.',
    tags: ['Multilingual', 'NLP', 'Accessibility'],
    content: `# Bilingual Support: English and Luganda Integration

## The Importance of Local Languages

Uganda has over 30 languages, but English and Luganda are the most widely used. Many taxpayers, particularly in rural areas, are more comfortable communicating in Luganda. Our commitment to bilingual support ensures financial services aren't gatekept by language.

## Implementation Strategy

### Language Detection
The system automatically detects whether input is in English or Luganda using:
- Character-level analysis
- Vocabulary-based classification
- Confidence scoring for ambiguous cases

### Translation Pipeline
For Luganda queries:
1. **Input Recognition**: Identify Luganda text
2. **Translation to English**: Use fine-tuned translation models
3. **Processing**: Standard English processing pipeline
4. **Response Generation**: Generate response in English
5. **Back-Translation**: Translate response back to Luganda
6. **Quality Check**: Verify translation maintains accuracy

### Voice Support
- **Speech Recognition**: ASR (Automatic Speech Recognition) for Luganda and English
- **Text-to-Speech**: TTS in both languages for audio responses
- **Pronunciation Optimization**: Language-specific phonetic processing

## Challenges and Solutions

### Challenge 1: Limited Training Data
**Solution**: Leveraged Makerere University AI Lab's open Luganda language resources and created synthetic training data through back-translation.

### Challenge 2: Domain-Specific Terminology
**Solution**: Built custom vocabularies for tax-related terms in both languages, trained with domain-specific examples.

### Challenge 3: Cultural Context
**Solution**: Adapted responses to reflect local business practices and tax procedures familiar to Ugandan taxpayers.

## User Experience

Users can:
- **Switch Languages Mid-Conversation**: Ask questions in one language, continue in another
- **Choose Input/Output Modes**: Type, voice, or mix both
- **Receive Localized Responses**: Tax advice formatted for Ugandan context
- **Access Supporting Documents**: Links to official URA materials in requested language

This approach makes tax information genuinely accessible, not just technically available.`,
  },
  {
    title: 'Security and Compliance: Protecting Taxpayer Data',
    slug: 'security-compliance',
    date: 'June 2026',
    category: 'Security',
    excerpt:
      'Deep dive into the security measures and compliance frameworks protecting sensitive taxpayer information in the URA Chatbot.',
    tags: ['Security', 'Privacy', 'Compliance'],
    content: `# Security and Compliance: Protecting Taxpayer Data

## Security by Design

We didn't add security as an afterthought. Instead, security and privacy were built into every layer from the start.

## Key Security Measures

### 1. Data Protection
- **Encryption in Transit**: TLS 1.3 for all communications
- **Encryption at Rest**: AES-256 encryption for stored data
- **PII Handling**: Automatic detection and redaction of personally identifiable information
- **Data Minimization**: Only collect and store necessary information

### 2. Authentication and Authorization
- **Multi-Factor Authentication**: Optional MFA for sensitive operations
- **Role-Based Access Control**: Different permission levels for agents and administrators
- **Session Management**: Secure token-based sessions with automatic expiration
- **Audit Logging**: Complete audit trail of all access and modifications

### 3. API Security
- **Rate Limiting**: Prevent abuse and DoS attacks
- **Input Validation**: Comprehensive validation of all user inputs
- **CORS Configuration**: Carefully controlled cross-origin requests
- **Content Security Policy**: Strict CSP headers to prevent injection attacks

### 4. Model Security
- **Prompt Injection Protection**: Detect and block adversarial prompts
- **Output Filtering**: Remove potentially harmful generated content
- **Resource Limits**: Prevent excessive resource consumption
- **Monitoring**: Real-time detection of unusual patterns

## Compliance Framework

### Uganda Data Protection Act (UDPA)
- Consent management for personal data processing
- User rights: access, rectification, erasure
- Data breach notification procedures
- Data Processing Agreements with all vendors

### Uganda National Data Protection and Privacy Act (NDPA)
- Privacy impact assessments
- Privacy-by-design principles
- Regular compliance audits
- Staff training on data protection

### OWASP Standards
- Regular penetration testing
- Vulnerability scanning and management
- Security code reviews
- Threat modeling

## Testing Results

During our security testing phase:
- **Simulated Attacks**: 100% of security attacks successfully blocked
- **Penetration Testing**: No critical vulnerabilities found
- **Vulnerability Scan**: All identified issues resolved before deployment
- **Compliance Audit**: Full compliance with UDPA and NDPA

## Ongoing Security

Security is continuous, not a one-time event:
- **Regular Updates**: Security patches applied immediately
- **Threat Monitoring**: 24/7 monitoring for suspicious activity
- **Incident Response**: Documented procedures for security incidents
- **Staff Training**: Regular training on security best practices for all team members

We believe taxpayers deserve assurance that their sensitive financial information is handled with care and protected by industry-leading security practices.`,
  },
  {
    title: 'Testing and Quality Assurance: Ensuring Reliability',
    slug: 'testing-qa',
    date: 'June 2026',
    category: 'Quality',
    excerpt:
      'Our comprehensive testing strategy ensuring the URA Chatbot delivers accurate, reliable answers to taxpayers.',
    tags: ['Testing', 'QA', 'Quality Assurance'],
    content: `# Testing and Quality Assurance: Ensuring Reliability

## Testing Strategy

We employed multiple testing methodologies to ensure system reliability and accuracy:

## Test Levels

### 1. Unit Testing
- Individual components tested in isolation
- Edge cases and error conditions covered
- Code coverage target: >80%

### 2. Integration Testing
- Components work correctly together
- Data flows properly through the system
- API endpoints return expected responses

### 3. End-to-End Testing
- Complete user workflows from query to response
- Multi-language support verified
- Mobile and web interfaces tested

### 4. Performance Testing
- Response time under normal load: <2 seconds
- Response time under peak load: <5 seconds
- Concurrent user handling: 1000+ simultaneous users

## Quality Metrics

### Answer Accuracy
- **Test Set Performance**: 94% of answers accurately grounded in URA documents
- **Confidence Scoring**: System confidence correlates with actual accuracy
- **Source Verification**: 99.8% of sources verified as legitimate

### Retrieval Accuracy
- **Document Retrieval**: 100% accuracy on test set
- **Relevance Ranking**: Top-3 retrieval accuracy: 96%
- **Speed**: Average retrieval time <100ms

### Response Quality
- **Response Time**: Average 1.2 seconds
- **P95 Response Time**: 3.8 seconds
- **Availability**: 99.9% uptime during testing

## Security Testing

### Attack Vectors Tested
- SQL injection attempts: All blocked
- Prompt injection attacks: All blocked
- Cross-site scripting (XSS): All blocked
- Cross-site request forgery (CSRF): All blocked
- Data exfiltration attempts: All blocked

### Result
**100% of security attacks successfully blocked** during testing

## User Acceptance Testing

- 50 real taxpayers tested the system
- Average satisfaction: 4.6/5.0
- Common feedback: "Accurate and helpful"
- No critical issues identified

## Automated Testing

- **CI/CD Integration**: Tests run on every code commit
- **Continuous Monitoring**: Production performance tracked 24/7
- **Automated Rollback**: Failed deployments automatically reversed

## Lessons Learned

1. Early testing catches issues before they affect users
2. Automated testing enables rapid iteration
3. User feedback is invaluable for real-world validation
4. Security testing should be thorough and continuous

Quality assurance isn't a phase—it's a fundamental part of our development process.`,
  },
  {
    title: 'Deployment and Operations: From Lab to Production',
    slug: 'deployment-operations',
    date: 'June 2026',
    category: 'Operations',
    excerpt:
      'How we deployed the URA Chatbot to production infrastructure and established operational best practices for maintainability.',
    tags: ['Deployment', 'DevOps', 'Operations'],
    content: `# Deployment and Operations: From Lab to Production

## Deployment Infrastructure

The URA Chatbot is deployed on **Makerere University's Crane Cloud**, a robust cloud infrastructure designed for academic and public sector projects.

### Architecture

\`\`\`
┌─────────────────┐
│  Load Balancer  │
└────────┬────────┘
         │
    ┌────┴────┐
    │          │
┌───▼──┐   ┌──▼───┐
│ API  │   │ API  │
│Pod 1 │   │Pod 2 │
└───┬──┘   └──┬───┘
    │         │
    └────┬────┘
         │
    ┌────▼────────┐
    │  Database   │
    │  & Cache    │
    └─────────────┘
\`\`\`

## Containerization

- **Docker**: Each component containerized for consistency
- **Kubernetes**: Orchestration across multiple machines
- **Auto-scaling**: Automatically scales based on demand
- **Health Checks**: Continuous monitoring of component health

## Deployment Process

### Stages

1. **Development**: Local testing and validation
2. **Staging**: Full replica of production for final testing
3. **Canary Deployment**: Roll out to 5% of traffic first
4. **Production**: Full deployment after validation
5. **Monitoring**: Continuous observation for issues

### Rollback Strategy

- Automated rollback on performance degradation
- Version control enables quick reversion
- Zero-downtime deployments

## Operations and Maintenance

### Monitoring

**Metrics Tracked**:
- Response time and latency
- Error rates and error types
- Resource utilization (CPU, memory)
- Number of concurrent users
- Answer quality scores
- Security incidents

**Alerting**:
- Automatic alerts for anomalies
- On-call rotation for critical issues
- Escalation procedures defined

### Log Management

- Centralized logging for all components
- 30-day retention of detailed logs
- Searchable logging for debugging
- PII redacted from all logs

### Updates and Patches

- Security patches applied within 24 hours
- Feature updates deployed during maintenance windows
- Automatic testing before production deployment

## Performance Targets

| Metric | Target | Actual |
|--------|--------|--------|
| Response Time | <2s | 1.2s avg |
| Availability | 99.9% | 99.95% |
| Error Rate | <0.1% | 0.02% |
| Max Concurrent Users | 1000 | Tested to 2000 |

## Cost Optimization

- **Right-sizing**: Containers sized appropriately for their workload
- **Auto-scaling**: Reduces costs during low-traffic periods
- **Caching**: Minimizes database queries and API calls
- **CDN Integration**: Serves static assets efficiently

## Documentation

- **Runbooks**: Step-by-step guides for common operations
- **Architecture Diagrams**: Visual representations of system components
- **API Documentation**: Complete OpenAPI/Swagger documentation
- **Troubleshooting Guide**: Solutions to common issues

## Disaster Recovery

- **Backups**: Automated daily backups with encrypted storage
- **Recovery Time Objective (RTO)**: <1 hour
- **Recovery Point Objective (RPO)**: <15 minutes
- **Disaster Recovery Testing**: Quarterly DR drills

The deployment infrastructure is designed to be reliable, scalable, and maintainable, ensuring the URA Chatbot continues serving taxpayers effectively.`,
  },
  {
    title: 'Deep Dive: Mpairwe Lauben - System Architecture, ML Processing & Fine-tuning',
    slug: 'contributor-mpairwe-lauben',
    date: 'June 2026',
    category: 'Team',
    excerpt:
      'How Mpairwe Lauben shaped the end-to-end system architecture and built the machine-learning core - model processing and LoRA fine-tuning - behind the URA Chatbot.',
    tags: ['Team', 'Architecture', 'Machine Learning', 'Fine-tuning'],
    content: `# Deep Dive: Mpairwe Lauben - System Architecture, ML Processing & Fine-tuning

## Introduction

Mpairwe Lauben owned the technical blueprint of the URA Chatbot and its machine-learning core. From the layered system architecture down to the LoRA adapters that teach the model Luganda, Lauben's work defines how every request flows through the system.

## System Architecture

Lauben designed a modular, layered architecture so each concern can scale and evolve independently:

- **Frontend Layer**: Web interface for taxpayers
- **API Layer**: Request orchestration and service coordination
- **Processing Layer**: Language detection, translation, normalization
- **Retrieval Layer**: Hybrid semantic and keyword search
- **Generation Layer**: LLM inference grounded in retrieved context
- **Integration Layer**: Connections to data and infrastructure services

This separation lets the team deploy, test, and optimize components in isolation while keeping clear contracts between them.

## ML Processing: the RAG Engine

The retrieval-augmented generation pipeline is the heart of the system, and Lauben architected its phases end to end:

**Phase 1: Query Processing**
- Language detection (English / Luganda)
- URA-specific abbreviation expansion
- Spelling correction and coreference resolution

**Phase 2: Semantic Cache**
- Cosine-similarity matching (threshold 0.92)
- Cached answers for repeat queries with configurable TTL

**Phase 3: Hybrid Retrieval**
- Dense vector search over embeddings
- BM25 sparse retrieval
- Reciprocal Rank Fusion to merge both

**Phase 4: Corrective RAG**
- Re-retrieval when average relevance falls below threshold
- Query expansion and broader fallback search

**Phase 5: Response Generation**
- Qwen3-8B inference (4-bit quantization)
- Multi-turn memory and citation generation

## Fine-tuning

To make a general model fluent in Ugandan tax language, Lauben fine-tuned it with parameter-efficient methods:

- **LoRA / PEFT adapters** layered on Qwen3-8B for domain and language adaptation
- **Luganda adapters** so translation and generation respect local phrasing
- **Quality gates** during training - faithfulness, answer relevancy, context precision/recall, and citation accuracy - to keep each new adapter honest before it ships

## Impact

Lauben's architecture and ML work delivered:
- A 94% accuracy rate grounded in official URA documents
- A pipeline that degrades gracefully and re-retrieves when unsure
- A model that speaks both English and Luganda in a tax-specific register
- A clean component boundary that the rest of the team could build on confidently`,
  },
  {
    title: 'Deep Dive: Olwol Philly - Backend Development & the TTS Pipeline',
    slug: 'contributor-olwol-philly',
    date: 'June 2026',
    category: 'Team',
    excerpt:
      'How Olwol Philly built the FastAPI backend that powers the URA Chatbot and engineered the text-to-speech pipeline that gives it a voice.',
    tags: ['Team', 'Backend', 'FastAPI', 'Text-to-Speech'],
    content: `# Deep Dive: Olwol Philly - Backend Development & the TTS Pipeline

## Introduction

Olwol Philly built the backbone of the URA Chatbot: the FastAPI service that ties the frontend, the retrieval engine, and the model together, plus the text-to-speech pipeline that lets the chatbot answer out loud.

## Backend Architecture

Philly designed a clean, layered FastAPI backend:

**API Layer**
- RESTful endpoints for chat, history, and analytics
- Streaming responses via Server-Sent Events (SSE)
- Request/response validation with Pydantic v2
- Rate limiting and authentication

**Service Layer**
- Orchestration of the RAG pipeline
- Query processing and response formatting
- Session and conversation handling

**Data Layer**
- PostgreSQL access with proper indexing and migrations
- Vector store (Qdrant) integration for retrieval
- Redis caching for hot paths

## The TTS Pipeline

Many taxpayers prefer to listen rather than read, so Philly built a text-to-speech pipeline for both supported languages:

- **Language-aware synthesis**: English and Luganda voices
- **Phonetic optimization**: language-specific pronunciation handling
- **Streaming audio**: responses spoken back as they are generated
- **Fallback handling**: graceful degradation to text when audio is unavailable

## Reliability

Philly made the backend dependable under load:
- Async request handling for high concurrency
- Connection pooling for the database and vector store
- Structured error handling and health-check endpoints
- Clear API contracts documented with OpenAPI/Swagger

## Impact

Philly's backend and TTS work delivered:
- A service tested to handle 1000+ concurrent users
- Sub-2-second average response times
- Spoken answers in English and Luganda for accessibility
- A stable, well-documented API the whole team built against`,
  },
  {
    title: 'Deep Dive: Rwemera David - Frontend Design & the Ingestion Pipeline',
    slug: 'contributor-rwemera-david',
    date: 'June 2026',
    category: 'Team',
    excerpt:
      'How Rwemera David designed the taxpayer-facing web experience and built the ingestion pipeline that keeps the URA knowledge base current.',
    tags: ['Team', 'Frontend', 'UX', 'Data Ingestion'],
    content: `# Deep Dive: Rwemera David - Frontend Design & the Ingestion Pipeline

## Introduction

Rwemera David shaped two ends of the system: the interface taxpayers actually touch, and the ingestion pipeline that feeds the knowledge base everything depends on.

## Frontend Design

David built a modern, accessible web experience:

- **Component architecture**: React with TypeScript and clean separation of concerns
- **Responsive, mobile-first layout**: works on the low-end phones most taxpayers use
- **Accessibility**: WCAG 2.1 AA compliance, semantic HTML, keyboard navigation
- **Dark and light themes**: comfortable reading in any environment
- **Real-time chat UI**: streaming responses with clear source citations

The design goal was to make a complex AI system feel as simple as sending a message.

## The Ingestion Pipeline

A retrieval system is only as good as the data behind it. David built the pipeline that collects and structures URA content:

**Crawling**
- Automated crawling of ura.go.ug
- Scheduled refreshes to keep the knowledge base current

**Extraction**
- PDF and CSV ingestion for forms, guides, and tax schedules
- Text normalization and clean-up

**Processing**
- Deduplication of overlapping content
- PII redaction before anything is indexed
- Chunking and embedding for semantic retrieval

## Impact

David's frontend and ingestion work delivered:
- An interface real taxpayers rated 4.6/5 in testing
- A continuously refreshed knowledge base sourced from official URA material
- Clean, deduplicated, privacy-safe data feeding the retrieval engine
- A bridge between raw URA documents and accurate, grounded answers`,
  },
  {
    title: 'Deep Dive: Okwel Edgar Mark - Optimization, STT Pipeline & Security',
    slug: 'contributor-okwel-edgar-mark',
    date: 'June 2026',
    category: 'Team',
    excerpt:
      'How Okwel Edgar Mark optimized performance across the stack, built the speech-to-text pipeline, and implemented the security guardrails protecting taxpayer data.',
    tags: ['Team', 'Performance', 'Speech-to-Text', 'Security'],
    content: `# Deep Dive: Okwel Edgar Mark - Optimization, STT Pipeline & Security

## Introduction

Okwel Edgar Mark made the URA Chatbot fast, gave it ears, and kept it safe. His work spans performance optimization, the speech-to-text pipeline, and the security implementation that protects sensitive taxpayer information.

## Optimization

Edgar tuned the system for speed and efficiency:

- **Caching strategy**: semantic and response caching to avoid repeated work
- **Latency reduction**: profiling hot paths and trimming overhead
- **Quantized inference**: 4-bit model serving for memory efficiency
- **Right-sizing and auto-scaling**: matching resources to real demand

The result is sub-2-second average responses that hold up under peak load.

## The STT Pipeline

So taxpayers can simply speak their questions, Edgar built the speech-to-text pipeline:

- **Automatic Speech Recognition** for English and Luganda
- **Whisper-based models** with adapters for local languages
- **Noise handling** for real-world phone audio
- **Streaming transcription** feeding straight into the RAG engine

## Security Implementation

Edgar built security into every layer, not as an afterthought:

**OWASP LLM Top 10 safeguards**
- LLM01: Prompt-injection detection
- LLM02: Insecure output handling
- LLM06: Sensitive-information disclosure prevention
- LLM08/09: Excessive-agency and overreliance mitigation

**Platform security**
- TLS 1.3 in transit and AES-256 at rest
- PII detection and redaction
- Input validation, rate limiting, and audit logging

## Impact

Edgar's work delivered:
- Sub-2-second responses sustained under load
- Voice input in English and Luganda via the STT pipeline
- 100% of simulated attacks blocked during security testing
- A system taxpayers can trust with sensitive information`,
  },
];
