import { create } from 'zustand';

export interface ConnectorDatabaseStats {
  database: string;
  status: string;
  taxpayers_count?: number;
  invoices_count?: number;
  credit_notes_count?: number;
  total_fiscalized_ugx?: number;
  total_vat_collected_ugx?: number;
  stock_items_count?: number;
  total_stock_units?: number;
  terminals_count?: number;
  manufacturers_count?: number;
  packaging_lines_count?: number;
  total_stamps_count?: number;
  genuine_stamps_count?: number;
  orders_count?: number;
  total_prn_fee_revenue_ugx?: number;
  damaged_declarations_count?: number;
  reconciled_spoiled_stamps?: number;
}

export interface ConnectorItem {
  id: string;
  system_name: string;
  name: string;
  description: string;
  version: string;
  system_type: string;
  tags: string[];
  connected: boolean;
  healthy: boolean;
  status: string;
  tools: string[];
  database: ConnectorDatabaseStats;
}

interface ConnectorState {
  connectors: ConnectorItem[];
  activeConnectorIds: string[];
  isModalOpen: boolean;
  inspectingConnectorId: string | null;
  databaseRecords: Record<string, Record<string, unknown>>;
  isLoading: boolean;
  error: string | null;

  // Actions
  openModal: (inspectId?: string) => void;
  closeModal: () => void;
  setInspectingId: (id: string | null) => void;
  toggleConnector: (id: string) => Promise<void>;
  fetchConnectors: () => Promise<void>;
  fetchDatabaseRecords: (id: string) => Promise<void>;
}

const DEFAULT_CONNECTORS: ConnectorItem[] = [
  {
    id: 'efris',
    system_name: 'efris',
    name: 'Electronic Fiscal Receipting & Invoicing System (EFRIS)',
    description:
      'Real-time URA fiscal document generation, 20-digit FDN validation, stock inventory ledger, and credit notes backed by independent efris_system.db.',
    version: '1.0.0',
    system_type: 'e-invoicing',
    tags: ['efris', 'vat', 'fiscal_invoice', 'fdn', 'inventory'],
    connected: true,
    healthy: true,
    status: 'active',
    tools: [
      'efris_fiscal_invoice',
      'efris_taxpayer_status',
      'efris_stock_management',
      'efris_credit_note',
    ],
    database: {
      database: 'efris_system.db',
      status: 'ONLINE',
      taxpayers_count: 5,
      invoices_count: 1,
      credit_notes_count: 0,
      total_fiscalized_ugx: 118000.0,
      total_vat_collected_ugx: 18000.0,
      stock_items_count: 3,
      total_stock_units: 7000,
      terminals_count: 3,
    },
  },
  {
    id: 'digital_tax_stamps',
    system_name: 'digital_tax_stamps',
    name: 'Digital Tax Stamps (DTS) / Kakasa Track & Trace',
    description:
      'Digital tracking solution for 10 gazetted excisable commodities, Kakasa mobile authenticity scanning, PLC line activations, and PRN stamp orders backed by independent dts_system.db.',
    version: '1.0.0',
    system_type: 'track_and_trace',
    tags: ['dts', 'kakasa', 'digital_tax_stamps', 'excise', 'track_and_trace'],
    connected: true,
    healthy: true,
    status: 'active',
    tools: [
      'dts_verify_stamp',
      'dts_order_stamps',
      'dts_activate_stamps',
      'dts_taxpayer_status',
    ],
    database: {
      database: 'dts_system.db',
      status: 'ONLINE',
      manufacturers_count: 4,
      packaging_lines_count: 5,
      total_stamps_count: 4,
      genuine_stamps_count: 3,
      orders_count: 0,
      total_prn_fee_revenue_ugx: 0.0,
      damaged_declarations_count: 0,
      reconciled_spoiled_stamps: 0,
    },
  },
  {
    id: 'ursb',
    system_name: 'ursb',
    name: 'Uganda Registration Services Bureau (URSB)',
    description:
      'Company incorporation, business names registry, Form 20 director records, and legal compliance checks backed by independent ursb_system.db.',
    version: '1.0.0',
    system_type: 'business_registry',
    tags: ['ursb', 'business_registration', 'incorporation', 'form_20'],
    connected: true,
    healthy: true,
    status: 'active',
    tools: [
      'ursb_verify_business',
      'ursb_register_business',
      'ursb_compliance_status',
    ],
    database: {
      database: 'ursb_system.db',
      status: 'ONLINE',
      taxpayers_count: 7,
      total_fiscalized_ugx: 0.0,
      stock_items_count: 6,
    },
  },
  {
    id: 'bwims',
    system_name: 'bwims',
    name: 'Bonded Warehouse Information Management System (BWIMS)',
    description:
      'Customs bonded cargo tracking under IM7 warehousing regime, statutory 9-month overstay alerts, and ex-warehouse IM4 clearances backed by independent bwims_system.db.',
    version: '1.0.0',
    system_type: 'customs_warehousing',
    tags: ['bwims', 'customs', 'bonded_warehouse', 'im7', 'im4'],
    connected: true,
    healthy: true,
    status: 'active',
    tools: [
      'bwims_consignment_status',
      'bwims_warehouse_inventory',
      'bwims_release_clearance',
    ],
    database: {
      database: 'bwims_system.db',
      status: 'ONLINE',
      packaging_lines_count: 4,
      total_stamps_count: 4,
      total_fiscalized_ugx: 6670000000.0,
    },
  },
  {
    id: 'tin_registration',
    system_name: 'tin_registration',
    name: 'URA Taxpayer Registration & Instant TIN System',
    description:
      'Instant Individual TIN issuance via citizen NIN, Non-Individual company TIN registration linked to URSB, and tax obligations enrollment backed by independent tin_system.db.',
    version: '1.0.0',
    system_type: 'taxpayer_registration',
    tags: ['tin', 'instant_tin', 'nin', 'registration', 'tax_obligations'],
    connected: true,
    healthy: true,
    status: 'active',
    tools: [
      'tin_search_verify',
      'tin_apply_individual',
      'tin_apply_non_individual',
      'tin_tax_obligations',
    ],
    database: {
      database: 'tin_system.db',
      status: 'ONLINE',
      taxpayers_count: 8,
      stock_items_count: 13,
    },
  },
  {
    id: 'payment_system',
    system_name: 'payment_system',
    name: 'URA e-Services > Make a Payment Suite',
    description:
      'Comprehensive e-Payment services: 12-digit PRN vouchers for all taxes and NTR/MDA fees, expired PRN reactivation, real-time bank clearance, card/mobile-money checkout, and advance motor vehicle tax backed by independent payments_system.db.',
    version: '1.0.0',
    system_type: 'payment_gateway',
    tags: ['payments', 'prn', 'bank_slip', 'mobile_money', 'visa', 'advance_tax'],
    connected: true,
    healthy: true,
    status: 'active',
    tools: [
      'payment_generate_prn',
      'payment_view_status',
      'payment_reactivate_prn',
      'payment_checkout_settle',
      'payment_verify_advance_tax',
    ],
    database: {
      database: 'payments_system.db',
      status: 'ONLINE',
      invoices_count: 5,
      total_fiscalized_ugx: 2598000.0,
      total_vat_collected_ugx: 648000.0,
    },
  },
];

export const useConnectorStore = create<ConnectorState>((set, get) => ({
  connectors: DEFAULT_CONNECTORS,
  activeConnectorIds: ['efris', 'digital_tax_stamps', 'ursb', 'bwims', 'tin_registration', 'payment_system'],
  isModalOpen: false,
  inspectingConnectorId: null,
  databaseRecords: {},
  isLoading: false,
  error: null,

  openModal: (inspectId) =>
    set({
      isModalOpen: true,
      inspectingConnectorId: inspectId ?? null,
    }),

  closeModal: () =>
    set({
      isModalOpen: false,
      inspectingConnectorId: null,
    }),

  setInspectingId: (id) => set({ inspectingConnectorId: id }),

  fetchConnectors: async () => {
    try {
      set({ isLoading: true, error: null });
      const res = await fetch('/api/v1/connectors', {
        headers: { Accept: 'application/json' },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.connectors) && data.connectors.length > 0) {
          const activeIds = data.connectors
            .filter((c: ConnectorItem) => c.connected)
            .map((c: ConnectorItem) => c.id);
          set({
            connectors: data.connectors,
            activeConnectorIds: activeIds.length > 0 ? activeIds : get().activeConnectorIds,
            isLoading: false,
          });
          return;
        }
      }
      set({ isLoading: false });
    } catch {
      // Fallback stays in place for offline or local client mocks
      set({ isLoading: false });
    }
  },

  toggleConnector: async (id: string) => {
    const { activeConnectorIds, connectors } = get();
    const isCurrentlyActive = activeConnectorIds.includes(id);
    const nextActive = isCurrentlyActive
      ? activeConnectorIds.filter((cid) => cid !== id)
      : [...activeConnectorIds, id];

    // Optimistic update
    set({
      activeConnectorIds: nextActive,
      connectors: connectors.map((c) =>
        c.id === id ? { ...c, connected: !isCurrentlyActive } : c
      ),
    });

    try {
      await fetch(`/api/v1/connectors/${encodeURIComponent(id)}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enable: !isCurrentlyActive }),
      });
    } catch {
      // Keep optimistic state locally
    }
  },

  fetchDatabaseRecords: async (id: string) => {
    try {
      const res = await fetch(`/api/v1/connectors/${encodeURIComponent(id)}/records?limit=6`, {
        headers: { Accept: 'application/json' },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok) {
          set((state) => ({
            databaseRecords: {
              ...state.databaseRecords,
              [id]: data,
            },
          }));
        }
      }
    } catch {
      // Ignored for offline mock
    }
  },
}));
