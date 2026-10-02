import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import { ConnectorsModal } from '../../components/ConnectorsModal';
import { useConnectorStore } from '../../store/useConnectorStore';

describe('ConnectorsModal', () => {
  beforeEach(() => {
    const store = useConnectorStore.getState();
    store.closeModal();
  });

  it('renders nothing when closed', () => {
    const { container } = render(<ConnectorsModal />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders all 6 connectors when opened', () => {
    useConnectorStore.getState().openModal();
    render(<ConnectorsModal />);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(
      screen.getByText(/System Connectors & Enterprise Databases/i)
    ).toBeInTheDocument();

    // Verify all 6 systems appear
    expect(screen.getByText(/Electronic Fiscal Receipting & Invoicing System \(EFRIS\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Digital Tax Stamps \(DTS\) \/ Kakasa/i)).toBeInTheDocument();
    expect(screen.getByText(/Uganda Registration Services Bureau \(URSB\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Bonded Warehouse Information Management System \(BWIMS\)/i)).toBeInTheDocument();
    expect(screen.getByText(/URA Taxpayer Registration & Instant TIN System/i)).toBeInTheDocument();
    expect(screen.getByText(/URA e-Services > Make a Payment Suite/i)).toBeInTheDocument();
  });

  it('allows toggling connect and disconnect', () => {
    useConnectorStore.getState().openModal();
    render(<ConnectorsModal />);

    const efrisCard = screen.getByText(/Electronic Fiscal Receipting & Invoicing System \(EFRIS\)/i).closest('div');
    expect(efrisCard).toBeInTheDocument();

    const disconnectBtns = screen.getAllByRole('button', { name: /Disconnect/i });
    expect(disconnectBtns.length).toBeGreaterThan(0);

    // Toggle disconnect
    fireEvent.click(disconnectBtns[0]);
    // State updates
    const activeIds = useConnectorStore.getState().activeConnectorIds;
    expect(activeIds.includes('efris')).toBe(false);
  });

  it('switches to database inspection tab', () => {
    useConnectorStore.getState().openModal('efris');
    render(<ConnectorsModal />);

    const dbTab = screen.getByRole('button', { name: /Inspect Independent Databases/i });
    fireEvent.click(dbTab);

    expect(screen.getByText(/Inspecting Database:/i)).toBeInTheDocument();
    expect(screen.getByText(/Independent SQLite Store:/i)).toBeInTheDocument();
  });
});
