import React, { memo } from 'react';
import type { ContextResource } from '../store/useChatStore';

interface ResourceCardsProps {
  resources: ContextResource[];
  title?: string;
}

function ResourceCardsInner({ resources, title = 'Official URA Forms, Templates & Sources' }: ResourceCardsProps) {
  if (!resources || resources.length === 0) return null;

  return (
    <div className="resource-cards-container" role="region" aria-label={title}>
      <div className="resource-cards-header">
        <span className="resource-cards-title-icon" aria-hidden="true">📂</span>
        <span className="resource-cards-title">{title}</span>
        <span className="resource-cards-count-badge">{resources.length}</span>
        <span className="resource-verified-banner" aria-label="Verified government source">✓ Official Verified Sources</span>
      </div>

      <div className="resource-cards-grid">
        {resources.map((res, idx) => {
          const isDownload = res.type === 'downloadable_form';
          const isOnlineForm = res.type === 'online_form';
          const isStatute = res.type === 'statutory_source';
          const format = (res.format || (isOnlineForm ? 'web' : 'pdf')).toLowerCase();

          return (
            <div
              key={res.id || idx}
              className={`resource-card is-${res.type || 'generic'}`}
              role="article"
            >
              <div className="resource-card-top">
                <span className={`resource-type-pill is-${res.type || 'generic'}`}>
                  {isDownload && '📥 Downloadable Form'}
                  {isOnlineForm && '🌐 Online Form'}
                  {isStatute && '⚖️ Statutory Law'}
                  {!isDownload && !isOnlineForm && !isStatute && '📖 Official Guide'}
                </span>

                <div className="resource-meta-group">
                  {res.effective_year && (
                    <span className="resource-year-tag" title="Effective statutory period">{res.effective_year}</span>
                  )}
                  {res.format && (
                    <span className={`resource-format-tag is-${format}`}>
                      .{format}
                    </span>
                  )}
                  {res.size && (
                    <span className="resource-size-tag">{res.size}</span>
                  )}
                </div>
              </div>

              <h4 className="resource-card-title">{res.title}</h4>

              {res.description && (
                <p className="resource-card-desc">{res.description}</p>
              )}

              {res.citation && (
                <div className="resource-citation-wrap">
                  <span className="resource-citation-label">Statutory Basis:</span>
                  <span className="resource-citation-text">{res.citation}</span>
                </div>
              )}

              {res.checklist && res.checklist.length > 0 && (
                <div className="resource-checklist-wrap">
                  <span className="resource-checklist-title">Checklist before submitting:</span>
                  <ul className="resource-checklist-list">
                    {res.checklist.map((item, i) => (
                      <li key={i}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="resource-card-actions">
                <a
                  href={res.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`resource-action-btn ${isDownload ? 'is-download' : 'is-portal'}`}
                  title={`${isDownload ? 'Download' : 'Open'}: ${res.title}`}
                >
                  {isDownload && <><span>Download Template</span> <span aria-hidden="true">⤓</span></>}
                  {isOnlineForm && <><span>Open Online Form</span> <span aria-hidden="true">↗</span></>}
                  {isStatute && <><span>View Statute</span> <span aria-hidden="true">↗</span></>}
                  {!isDownload && !isOnlineForm && !isStatute && <><span>Access Guide</span> <span aria-hidden="true">↗</span></>}
                </a>
              </div>

              {res.source_domain && (
                <div className="resource-domain-footnote">
                  <span>Source: {res.source_domain}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export const ResourceCards = memo(ResourceCardsInner);
export default ResourceCards;
