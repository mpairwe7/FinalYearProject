import React, { memo } from 'react';
import { WorkflowState } from '../store/useChatStore';
import { useTranslation } from '../lib/i18n';
import { safePortalUrl } from '../lib/safeUrl';

interface WorkflowStepperProps {
  workflow: WorkflowState;
  onSelectOption?: (option: string) => void;
}

function WorkflowStepperInner({ workflow, onSelectOption }: WorkflowStepperProps) {
  const t = useTranslation();
  if (!workflow || !workflow.name) return null;

  const isActive = workflow.status === 'active';
  const isCompleted = workflow.status === 'completed';
  const total = workflow.total_steps || (workflow.all_steps?.length ?? 0);
  const currentIdx = workflow.step_index || ((workflow.current_step_idx ?? 0) + 1);
  const portalHref = safePortalUrl(workflow.portal_action?.url);

  return (
    <div
      className={`workflow-stepper-container ${isActive ? 'is-active' : isCompleted ? 'is-completed' : 'is-cancelled'}`}
      role="region"
      aria-label={t('workflow.region', { name: workflow.name })}
    >
      <div className="workflow-stepper-header">
        <div className="workflow-title-wrap">
          <span className="workflow-badge" aria-hidden="true">
            {isCompleted ? '✓' : '⚡'}
          </span>
          <span className="workflow-name">{workflow.name}</span>
          {isActive && total > 0 && (
            <span className="workflow-progress-pill" aria-label={t('workflow.step', { current: currentIdx, total })}>
              {t('workflow.step', { current: currentIdx, total })}
            </span>
          )}
          {workflow.resume_code && (
            <span className="workflow-resume-pill" data-testid="workflow-resume-code">
              {t('workflow.resumeCode', { code: workflow.resume_code })}
            </span>
          )}
          {isCompleted && (
            <span className="workflow-status-pill is-complete">{t('workflow.completed')}</span>
          )}
        </div>
      </div>

      {(workflow.journey_gps?.is_paused_for_tangent ||
        (workflow.journey_gps?.parent_goals && workflow.journey_gps.parent_goals.length > 0)) && (
        <div className="workflow-journey-gps-banner" data-testid="workflow-journey-gps">
          {workflow.journey_gps?.is_paused_for_tangent && (
            <span>{t('workflow.journeyGpsPaused')}</span>
          )}
          {workflow.journey_gps?.parent_goals && workflow.journey_gps.parent_goals.length > 0 && (
            <span className="workflow-journey-gps-stack" data-testid="workflow-journey-gps-stack">
              {workflow.journey_gps.parent_goals.map((pg) => pg.workflow_id).join(' → ')} → {workflow.id}
            </span>
          )}
        </div>
      )}

      {workflow.prerequisites && workflow.prerequisites.length > 0 && (
        <div className="workflow-prerequisites-box" data-testid="workflow-prerequisites">
          <div className="workflow-prerequisites-title">{t('workflow.prerequisites')}</div>
          <ul className="workflow-prerequisites-list">
            {workflow.prerequisites.map((pr) => (
              <li key={pr.id} className={`workflow-prereq-item is-${pr.status}`}>
                <span aria-hidden="true">
                  {pr.status === 'satisfied' ? '✓' : pr.status === 'missing' ? '⚠' : '○'}
                </span>{' '}
                <span>{pr.label}</span>
                {isActive && pr.status === 'missing' && pr.remediation_action && onSelectOption && (
                  <button
                    type="button"
                    className="workflow-prereq-remediate-btn"
                    onClick={() => onSelectOption(pr.remediation_action!)}
                  >
                    {pr.remediation_action}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {workflow.all_steps && workflow.all_steps.length > 0 && (
        <div className="workflow-step-track" role="list" aria-label={t('workflow.stepProgress')}>
          {workflow.all_steps.map((st, i) => {
            const stepNum = i + 1;
            const isCur = st.status === 'current';
            const isDone = st.status === 'completed';
            return (
              <div
                key={st.id || i}
                className={`workflow-step-node ${isCur ? 'is-current' : isDone ? 'is-done' : 'is-pending'}`}
                role="listitem"
                aria-current={isCur ? 'step' : undefined}
                title={`${stepNum}. ${st.title} (${st.status})`}
              >
                <div className="workflow-step-dot" aria-hidden="true">
                  {isDone ? '✓' : stepNum}
                </div>
                <span className="workflow-step-label">{st.title}</span>
                {i < workflow.all_steps!.length - 1 && <div className="workflow-step-line" aria-hidden="true" />}
              </div>
            );
          })}
        </div>
      )}

      {isActive && workflow.step_title && (
        <div className="workflow-current-step-banner">
          <span className="workflow-step-heading">
            {t('workflow.currentStep', { title: workflow.step_title })}
          </span>
        </div>
      )}

      {isActive && workflow.scaffolding?.hint && (
        <div className="workflow-scaffolding-banner" data-testid="workflow-scaffolding">
          <span>{t('workflow.scaffoldingHint', { hint: workflow.scaffolding.hint })}</span>
          {(!workflow.options || workflow.options.length === 0) &&
            workflow.scaffolding.helper_actions &&
            workflow.scaffolding.helper_actions.length > 0 &&
            onSelectOption && (
              <div className="workflow-scaffolding-actions">
                {workflow.scaffolding.helper_actions.map((act) => (
                  <button
                    key={act}
                    type="button"
                    className="workflow-option-btn"
                    onClick={() => onSelectOption(act)}
                  >
                    {act}
                  </button>
                ))}
              </div>
            )}
        </div>
      )}

      {workflow.form_draft && workflow.form_draft.fields && workflow.form_draft.fields.length > 0 && (
        <div className="workflow-form-draft" data-testid="workflow-form-draft">
          <div className="workflow-form-draft-header">
            {t('workflow.formDraft', {
              code: workflow.form_draft.form_code,
              pct: workflow.form_draft.completion_pct,
            })}
          </div>
          <div className="workflow-form-draft-grid">
            {workflow.form_draft.fields.map((fld) => (
              <div key={fld.slot} className={`workflow-form-field is-${fld.status}`}>
                <span className="workflow-form-box">{fld.box}</span>
                <span className="workflow-form-label">{fld.label}</span>
                <span className="workflow-form-val">{fld.display_value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {workflow.step_up_auth && (
        <div className="workflow-step-up-auth" data-testid="workflow-step-up-auth">
          <div className="workflow-step-up-title">
            {t('workflow.stepUpAuth', { count: workflow.step_up_auth.preserved_slot_count ?? 0 })}
          </div>
          <p className="workflow-step-up-reason">{workflow.step_up_auth.reason}</p>
          {isActive && (workflow.step_up_auth.remediation_action || workflow.step_up_auth.register_action) &&
            onSelectOption && (
              <div className="workflow-step-up-actions">
                <button
                  type="button"
                  className="workflow-option-btn workflow-step-up-action-btn"
                  data-testid="workflow-step-up-action-btn"
                  onClick={() =>
                    onSelectOption(
                      (workflow.step_up_auth!.remediation_action ||
                        workflow.step_up_auth!.register_action)!,
                    )
                  }
                >
                  {workflow.step_up_auth.remediation_action || workflow.step_up_auth.register_action}
                </button>
              </div>
            )}
        </div>
      )}

      {isActive && workflow.options && workflow.options.length > 0 && onSelectOption && (
        <div className="workflow-options-group" role="group" aria-label={t('workflow.quickOptions')}>
          <span className="workflow-options-prompt">{t('workflow.chooseOption')}</span>
          <div className="workflow-options-buttons">
            {workflow.options.map((opt, index) => {
              const label = workflow.option_labels?.[index] ?? opt;
              return (
                <button
                  key={opt}
                  type="button"
                  className="workflow-option-btn"
                  onClick={() => onSelectOption(opt)}
                  title={t('workflow.selectOption', { label })}
                >
                  <span className="workflow-option-bullet" aria-hidden="true">●</span>
                  <span className="workflow-option-text">{label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="workflow-footer-actions">
        {portalHref && (
          <a
            href={portalHref}
            target="_blank"
            rel="noopener noreferrer"
            className="workflow-portal-btn"
            title={t('workflow.openPortal', { label: workflow.portal_action?.label || t('workflow.openPortalDefault') })}
          >
            {workflow.portal_action?.label || t('workflow.openPortalDefault')}
          </a>
        )}
        {isActive && onSelectOption && (
          <button
            type="button"
            className="workflow-cancel-btn"
            onClick={() => onSelectOption('cancel')}
            title={t('workflow.cancel')}
          >
            {t('workflow.cancel')}
          </button>
        )}
      </div>
    </div>
  );
}

export const WorkflowStepper = memo(WorkflowStepperInner);
export default WorkflowStepper;
