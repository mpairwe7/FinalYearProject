import React, { memo } from 'react';
import { WorkflowState } from '../store/useChatStore';
import ResourceCards from './ResourceCards';

interface WorkflowStepperProps {
  workflow: WorkflowState;
  onSelectOption?: (option: string) => void;
}

function WorkflowStepperInner({ workflow, onSelectOption }: WorkflowStepperProps) {
  if (!workflow || !workflow.name) return null;

  const isActive = workflow.status === 'active';
  const isCompleted = workflow.status === 'completed';
  const total = workflow.total_steps || (workflow.all_steps?.length ?? 0);
  const currentIdx = workflow.step_index || ((workflow.current_step_idx ?? 0) + 1);

  return (
    <div
      className={`workflow-stepper-container ${isActive ? 'is-active' : isCompleted ? 'is-completed' : 'is-cancelled'}`}
      role="region"
      aria-label={`Guided workflow: ${workflow.name}`}
    >
      <div className="workflow-stepper-header">
        <div className="workflow-title-wrap">
          <span className="workflow-badge" aria-hidden="true">
            {isCompleted ? '✓' : '⚡'}
          </span>
          <span className="workflow-name">{workflow.name}</span>
          {isActive && total > 0 && (
            <span className="workflow-progress-pill" aria-label={`Step ${currentIdx} of ${total}`}>
              Step {currentIdx} of {total}
            </span>
          )}
          {isCompleted && (
            <span className="workflow-status-pill is-complete">Completed</span>
          )}
        </div>
      </div>

      {workflow.all_steps && workflow.all_steps.length > 0 && (
        <div className="workflow-step-track" role="list" aria-label="Step progress">
          {workflow.all_steps.map((st, i) => {
            const stepNum = i + 1;
            const isCur = st.status === 'current';
            const isDone = st.status === 'completed';
            return (
              <div
                key={st.id || i}
                className={`workflow-step-node ${isCur ? 'is-current' : isDone ? 'is-done' : 'is-pending'}`}
                role="listitem"
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
          <span className="workflow-step-heading">Current step: <strong>{workflow.step_title}</strong></span>
        </div>
      )}

      {isActive && workflow.options && workflow.options.length > 0 && onSelectOption && (
        <div className="workflow-options-group" role="group" aria-label="Quick options">
          <span className="workflow-options-prompt">Choose an option to advance:</span>
          <div className="workflow-options-buttons">
            {workflow.options.map((opt) => (
              <button
                key={opt}
                type="button"
                className="workflow-option-btn"
                onClick={() => onSelectOption(opt)}
                title={`Select: ${opt}`}
              >
                <span className="workflow-option-bullet" aria-hidden="true">●</span>
                <span className="workflow-option-text">{opt}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {workflow.resources && workflow.resources.length > 0 && (
        <ResourceCards resources={workflow.resources} title="Required Forms & Source References" />
      )}

      <div className="workflow-footer-actions">
        {workflow.portal_action?.url && (
          <a
            href={workflow.portal_action.url}
            target="_blank"
            rel="noopener noreferrer"
            className="workflow-portal-btn"
            title={`Open official portal: ${workflow.portal_action.label || 'URA Portal'}`}
          >
            {workflow.portal_action.label || 'Open URA Portal ↗'}
          </a>
        )}
        {isActive && onSelectOption && (
          <button
            type="button"
            className="workflow-cancel-btn"
            onClick={() => onSelectOption('cancel')}
            title="Cancel this guided workflow"
          >
            Cancel workflow
          </button>
        )}
      </div>
    </div>
  );
}

export const WorkflowStepper = memo(WorkflowStepperInner);
export default WorkflowStepper;
