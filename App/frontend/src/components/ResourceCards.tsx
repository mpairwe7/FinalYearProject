import React, { memo } from 'react';
import { useTranslation } from '../lib/i18n';
import type { ContextResource } from '../store/useChatStore';

interface ResourceCardsProps {
  resources: ContextResource[];
  title?: string;
}

function ResourceCardsInner({ resources, title }: ResourceCardsProps) {
  const t = useTranslation();
  if (!resources || resources.length === 0) return null;
  const heading = title ?? t('resource.title');

  return (
    <div className="resource-cards-container" role="region" aria-label={heading}>
      <div className="resource-cards-header">
        <span className="resource-cards-title-icon" aria-hidden="true">📂</span>
        <span className="resource-cards-title">{heading}</span>
        <span className="resource-cards-count-badge">{resources.length}</span>
        <span className="resource-verified-banner" aria-label={t('resource.verified')}>✓ {t('resource.verified')}</span>
      </div>

      <div className="resource-cards-grid">
        {resources.map((res, idx) => {
          const isDownload = res.type === 'downloadable_form';
          const isOnlineForm = res.type === 'online_form';
          const isStatute = res.type === 'statutory_source';
          const format = (res.format || (isOnlineForm ? 'web' : 'pdf')).toLowerCase();
          const actionLabel = t(
            isDownload
              ? 'resource.action.download'
              : isOnlineForm
                ? 'resource.action.online'
                : isStatute
                  ? 'resource.action.statute'
                  : 'resource.action.guide',
          );
          const actionIcon = isDownload ? '⤓' : '↗';
          const actionClass = isDownload ? 'is-download' : 'is-portal';

          return (
            <div
              key={res.id || idx}
              className={`resource-card is-${res.type || 'generic'}`}
              role="article"
            >
              <div className="resource-card-top">
                <span className={`resource-type-pill is-${res.type || 'generic'}`}>
                  {isDownload && `📥 ${t('resource.type.download')}`}
                  {isOnlineForm && `🌐 ${t('resource.type.online')}`}
                  {isStatute && `⚖️ ${t('resource.type.statute')}`}
                  {!isDownload && !isOnlineForm && !isStatute && `📖 ${t('resource.type.guide')}`}
                </span>

                <div className="resource-meta-group">
                  {res.effective_year && (
                    <span className="resource-year-tag" title={t('resource.effectivePeriod')}>{res.effective_year}</span>
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
                  <span className="resource-citation-label">{t('resource.statutoryBasis')}</span>
                  <span className="resource-citation-text">{res.citation}</span>
                </div>
              )}

              {res.checklist && res.checklist.length > 0 && (
                <div className="resource-checklist-wrap">
                  <span className="resource-checklist-title">{t('resource.checklist')}</span>
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
                  className={`resource-action-btn ${actionClass}`}
                  title={`${actionLabel}: ${res.title}`}
                >
                  <span>{actionLabel}</span> <span aria-hidden="true">{actionIcon}</span>
                </a>
              </div>

              {res.source_domain && (
                <div className="resource-domain-footnote">
                  <span>{t('resource.source', { domain: res.source_domain })}</span>
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
