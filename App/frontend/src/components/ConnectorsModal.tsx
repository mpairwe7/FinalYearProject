'use client';

import React, { useEffect, useState } from 'react';
import {
  BwimsLogo,
  CloseIcon,
  DatabaseIcon,
  DtsLogo,
  EfrisLogo,
  EyeIcon,
  FileIcon,
  PaymentLogo,
  PlugIcon,
  ReceiptIcon,
  ShieldCheckIcon,
  StampIcon,
  TinLogo,
  UrsbLogo,
} from './Icons';
import { useConnectorStore } from '../store/useConnectorStore';

interface InvoiceRow {
  fdn: string;
  seller_name: string;
  buyer_name?: string | null;
  gross_amount: number | string;
  tax_amount: number | string;
  status: string;
}

interface StampRow {
  stamp_code: string;
  product_category: string;
  brand_name: string;
  manufacturer_name: string;
  production_date: string;
  status: string;
}

interface EntityRow {
  registration_number: string;
  business_name: string;
  entity_type: string;
  district: string;
  registration_date: string;
  status: string;
}

interface ConsignmentRow {
  entry_number: string;
  importer_name: string;
  warehouse_code: string;
  goods_description: string;
  cif_value_ugx: number | string;
  status: string;
}

interface TaxpayerRow {
  tin: string;
  legal_name: string;
  category: string;
  district: string;
  registration_date: string;
  status: string;
}

interface PrnRow {
  prn: string;
  taxpayer_name: string;
  tax_head: string;
  amount_ugx: number | string;
  expiry_date: string;
  status: string;
}

function getConnectorIcon(id: string) {
  switch (id) {
    case 'efris':
      return <EfrisLogo size={32} />;
    case 'digital_tax_stamps':
      return <DtsLogo size={32} />;
    case 'ursb':
      return <UrsbLogo size={32} />;
    case 'bwims':
      return <BwimsLogo size={32} />;
    case 'tin_registration':
      return <TinLogo size={32} />;
    case 'payment_system':
      return <PaymentLogo size={32} />;
    default:
      return <ShieldCheckIcon size={28} />;
  }
}

function getConnectorBadgeColor(type: string) {
  switch (type) {
    case 'e-invoicing':
      return 'bg-blue-500/15 text-blue-400 border-blue-500/30';
    case 'track_and_trace':
      return 'bg-amber-500/15 text-amber-400 border-amber-500/30';
    case 'business_registry':
      return 'bg-purple-500/15 text-purple-400 border-purple-500/30';
    case 'customs_warehousing':
      return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
    case 'taxpayer_registration':
      return 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30';
    case 'payment_gateway':
    default:
      return 'bg-rose-500/15 text-rose-400 border-rose-500/30';
  }
}

export function ConnectorsModal() {
  const {
    connectors,
    activeConnectorIds,
    isModalOpen,
    inspectingConnectorId,
    databaseRecords,
    closeModal,
    setInspectingId,
    toggleConnector,
    fetchConnectors,
    fetchDatabaseRecords,
  } = useConnectorStore();

  const [userSelectedTab, setUserSelectedTab] = useState<'connectors' | 'database' | 'external' | null>(null);
  const activeTab = userSelectedTab ?? (inspectingConnectorId ? 'database' : 'connectors');

  const [extName, setExtName] = useState('');
  const [extUrl, setExtUrl] = useState('');
  const [extKey, setExtKey] = useState('');
  const [extType, setExtType] = useState('external_mcp');
  const [extStatus, setExtStatus] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);

  const handleRegisterExternal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!extName.trim() || !extUrl.trim()) return;
    setIsRegistering(true);
    setExtStatus(null);
    try {
      const res = await fetch('/api/v1/connectors/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: extName.trim(),
          endpoint_url: extUrl.trim(),
          api_key: extKey.trim(),
          system_type: extType,
          display_name: extName.trim(),
          description: `External API/MCP connector at ${extUrl.trim()}`,
        }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        setExtStatus(`Successfully connected "${extName}"!`);
        fetchConnectors();
        setTimeout(() => {
          setUserSelectedTab('connectors');
          setExtStatus(null);
          setExtName('');
          setExtUrl('');
          setExtKey('');
        }, 1200);
      } else {
        setExtStatus(`Registration failed: ${data.detail || data.error || 'Server error'}`);
      }
    } catch (err: unknown) {
      setExtStatus(`Connection error: ${err instanceof Error ? err.message : 'Network error'}`);
    } finally {
      setIsRegistering(false);
    }
  };

  useEffect(() => {
    if (isModalOpen) {
      fetchConnectors();
    }
  }, [isModalOpen, fetchConnectors]);

  useEffect(() => {
    if (inspectingConnectorId) {
      fetchDatabaseRecords(inspectingConnectorId);
    }
  }, [inspectingConnectorId, fetchDatabaseRecords]);

  if (!isModalOpen) return null;

  const currentInspectingConnector =
    connectors.find((c) => c.id === inspectingConnectorId) ?? connectors[0];
  const inspectingData = currentInspectingConnector
    ? (databaseRecords[currentInspectingConnector.id] as Record<string, unknown> | undefined)
    : undefined;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="connectors-modal-title"
    >
      <div className="relative w-full max-w-4xl max-h-[90vh] flex flex-col bg-neutral-900 border border-neutral-700/80 rounded-2xl shadow-2xl overflow-hidden text-neutral-100">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-neutral-800 bg-neutral-900/90">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
              <PlugIcon size={22} />
            </div>
            <div>
              <h2 id="connectors-modal-title" className="text-lg font-semibold text-white tracking-tight">
                System Connectors &amp; Enterprise Databases
              </h2>
              <p className="text-xs text-neutral-400">
                Inspired by Grok app connectors. Each service runs an independent SQLite database for realistic URA automation.
              </p>
            </div>
          </div>
          <button
            onClick={closeModal}
            className="p-2 text-neutral-400 hover:text-white rounded-lg hover:bg-neutral-800 transition"
            aria-label="Close dialog"
          >
            <CloseIcon />
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex items-center gap-3 px-6 pt-3 border-b border-neutral-800 text-sm font-medium">
          <button
            onClick={() => setUserSelectedTab('connectors')}
            className={`pb-3 px-1 border-b-2 transition ${
              activeTab === 'connectors'
                ? 'border-emerald-500 text-emerald-400 font-semibold'
                : 'border-transparent text-neutral-400 hover:text-neutral-200'
            }`}
          >
            Available Connectors ({connectors.length})
          </button>
          <button
            onClick={() => {
              setUserSelectedTab('database');
              if (currentInspectingConnector) {
                fetchDatabaseRecords(currentInspectingConnector.id);
              }
            }}
            className={`pb-3 px-1 border-b-2 transition flex items-center gap-1.5 ${
              activeTab === 'database'
                ? 'border-emerald-500 text-emerald-400 font-semibold'
                : 'border-transparent text-neutral-400 hover:text-neutral-200'
            }`}
          >
            <DatabaseIcon size={15} />
            Inspect Independent Databases
          </button>
          <button
            onClick={() => setUserSelectedTab('external')}
            className={`pb-3 px-1 border-b-2 transition flex items-center gap-1.5 ${
              activeTab === 'external'
                ? 'border-emerald-500 text-emerald-400 font-semibold'
                : 'border-transparent text-neutral-400 hover:text-neutral-200'
            }`}
          >
            <PlugIcon size={14} />
            Connect External API / MCP
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {activeTab === 'connectors' ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {connectors.map((c) => {
                const isConnected = activeConnectorIds.includes(c.id);
                return (
                  <div
                    key={c.id}
                    className={`flex flex-col justify-between p-5 rounded-xl border transition-all duration-200 ${
                      isConnected
                        ? 'bg-neutral-800/60 border-neutral-700 shadow-md'
                        : 'bg-neutral-900/40 border-neutral-800/80 opacity-75'
                    }`}
                  >
                    <div>
                      <div className="flex items-start justify-between gap-3 mb-3">
                        <div className="flex items-center gap-3">
                          <div className="p-2.5 rounded-lg bg-neutral-800 border border-neutral-700 text-emerald-400">
                            {getConnectorIcon(c.id)}
                          </div>
                          <div>
                            <h3 className="font-semibold text-white text-sm leading-snug">{c.name}</h3>
                            <span
                              className={`inline-block px-2 py-0.5 mt-1 text-[11px] font-medium rounded-full border ${getConnectorBadgeColor(
                                c.system_type
                              )}`}
                            >
                              {c.system_type.replace('_', ' ').toUpperCase()}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span
                            className={`w-2 h-2 rounded-full ${
                              isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-neutral-500'
                            }`}
                          />
                          <span className="text-[11px] font-medium text-neutral-300">
                            {isConnected ? 'Connected' : 'Disconnected'}
                          </span>
                        </div>
                      </div>

                      <p className="text-xs text-neutral-300 mb-4 leading-relaxed line-clamp-2">
                        {c.description}
                      </p>

                      {/* Independent Database Metrics Indicator */}
                      <div className="p-3 mb-4 rounded-lg bg-neutral-950/60 border border-neutral-800 text-[11px] text-neutral-300 space-y-1.5">
                        <div className="flex items-center justify-between text-neutral-400 font-mono text-[10px]">
                          <span className="flex items-center gap-1">
                            <DatabaseIcon size={12} />
                            {c.database?.database || `${c.id}_system.db`}
                          </span>
                          <span className="text-emerald-400">STATUS: {c.database?.status || 'ONLINE'}</span>
                        </div>

                        {c.id === 'efris' && (
                          <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                            <div>Taxpayers: <strong className="text-white">{c.database?.taxpayers_count ?? 5}</strong></div>
                            <div>Invoices: <strong className="text-white">{c.database?.invoices_count ?? 1}</strong></div>
                            <div>Fiscalized: <strong className="text-white">UGX {(c.database?.total_fiscalized_ugx ?? 118000).toLocaleString()}</strong></div>
                            <div>Stock Items: <strong className="text-white">{c.database?.stock_items_count ?? 3}</strong></div>
                          </div>
                        )}

                        {c.id === 'digital_tax_stamps' && (
                          <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                            <div>Manufacturers: <strong className="text-white">{c.database?.manufacturers_count ?? 4}</strong></div>
                            <div>Packaging Lines: <strong className="text-white">{c.database?.packaging_lines_count ?? 5}</strong></div>
                            <div>Stamps Tracked: <strong className="text-white">{c.database?.total_stamps_count ?? 4}</strong></div>
                            <div>Kakasa Status: <strong className="text-emerald-400">GENUINE</strong></div>
                          </div>
                        )}

                        {c.id === 'ursb' && (
                          <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                            <div>Registered Entities: <strong className="text-white">{c.database?.taxpayers_count ?? 7}</strong></div>
                            <div>Form 20 Directors: <strong className="text-white">{c.database?.stock_items_count ?? 6}</strong></div>
                            <div>Annual Returns: <strong className="text-emerald-400">UP TO DATE</strong></div>
                            <div>TIN Prerequisite: <strong className="text-emerald-400">VERIFIED</strong></div>
                          </div>
                        )}

                        {c.id === 'bwims' && (
                          <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                            <div>Bonded Warehouses: <strong className="text-white">{c.database?.packaging_lines_count ?? 4}</strong></div>
                            <div>IM7 Entries: <strong className="text-white">{c.database?.total_stamps_count ?? 4}</strong></div>
                            <div>Bonded CIF: <strong className="text-white">UGX 6.67B</strong></div>
                            <div>Overstay Risk: <strong className="text-amber-400">MONITORED</strong></div>
                          </div>
                        )}

                        {c.id === 'tin_registration' && (
                          <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                            <div>Taxpayers: <strong className="text-white">{c.database?.taxpayers_count ?? 8}</strong></div>
                            <div>Tax Heads: <strong className="text-white">{c.database?.stock_items_count ?? 13}</strong></div>
                            <div>Instant NIN: <strong className="text-emerald-400">NIRA ACTIVE</strong></div>
                            <div>URSB Integration: <strong className="text-emerald-400">LINKED</strong></div>
                          </div>
                        )}

                        {c.id === 'payment_system' && (
                          <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                            <div>PRNs Generated: <strong className="text-white">{c.database?.invoices_count ?? 5}</strong></div>
                            <div>Cleared Revenue: <strong className="text-white">UGX 2.59M</strong></div>
                            <div>Card &amp; MoMo: <strong className="text-emerald-400">ACTIVE</strong></div>
                            <div>Advance Tax PSV: <strong className="text-emerald-400">COMPLIANT</strong></div>
                          </div>
                        )}
                      </div>

                      {/* Tool Tags */}
                      <div className="flex flex-wrap gap-1 mb-4">
                        {c.tools.slice(0, 3).map((t) => (
                          <span
                            key={t}
                            className="px-1.5 py-0.5 rounded bg-neutral-800 text-[10px] text-neutral-400 font-mono"
                          >
                            {t}
                          </span>
                        ))}
                        {c.tools.length > 3 && (
                          <span className="px-1.5 py-0.5 rounded bg-neutral-800 text-[10px] text-neutral-400">
                            +{c.tools.length - 3} more
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 pt-2 border-t border-neutral-800">
                      <button
                        onClick={() => {
                          setInspectingId(c.id);
                          setUserSelectedTab('database');
                          fetchDatabaseRecords(c.id);
                        }}
                        className="px-3 py-1.5 rounded-lg border border-neutral-700 bg-neutral-800/80 text-xs font-medium text-neutral-300 hover:text-white hover:bg-neutral-700 transition flex items-center gap-1.5"
                      >
                        <EyeIcon size={14} />
                        View DB
                      </button>
                      <button
                        onClick={() => toggleConnector(c.id)}
                        className={`flex-1 px-4 py-1.5 rounded-lg text-xs font-semibold transition ${
                          isConnected
                            ? 'bg-neutral-700/60 hover:bg-red-500/20 hover:text-red-300 text-neutral-200 border border-neutral-600/60'
                            : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm'
                        }`}
                      >
                        {isConnected ? 'Disconnect' : 'Connect System'}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : activeTab === 'database' ? (
            /* Database Records Inspector Tab */
            <div className="space-y-4">
              <div className="flex items-center justify-between pb-3 border-b border-neutral-800">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-neutral-400">Inspecting Database:</span>
                  <select
                    className="bg-neutral-800 border border-neutral-700 text-white rounded-lg px-2.5 py-1 text-xs font-mono"
                    value={currentInspectingConnector?.id}
                    onChange={(e) => {
                      setInspectingId(e.target.value);
                      fetchDatabaseRecords(e.target.value);
                    }}
                  >
                    {connectors.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} ({c.database?.database || `${c.id}_system.db`})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="text-[11px] font-mono text-emerald-400 flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  Independent SQLite Store: data_store/{currentInspectingConnector?.database?.database || `${currentInspectingConnector?.id}_system.db`}
                </div>
              </div>

              {inspectingData ? (
                <div className="space-y-4 text-xs font-mono">
                  {/* Invoices table if EFRIS */}
                  {Array.isArray(inspectingData.invoices) && (
                    <div>
                      <h4 className="text-xs font-semibold text-neutral-300 mb-2 font-sans flex items-center gap-1.5">
                        <ReceiptIcon size={14} />
                        Issued Fiscal Documents (efris_invoices)
                      </h4>
                      <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-neutral-950">
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="bg-neutral-900 border-b border-neutral-800 text-[10px] text-neutral-400">
                              <th className="p-2">FDN (20-Digit)</th>
                              <th className="p-2">Seller</th>
                              <th className="p-2">Buyer</th>
                              <th className="p-2">Gross (UGX)</th>
                              <th className="p-2">VAT (18%)</th>
                              <th className="p-2">Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(inspectingData.invoices as InvoiceRow[]).map((inv) => (
                              <tr key={inv.fdn} className="border-b border-neutral-900 hover:bg-neutral-900/50">
                                <td className="p-2 text-emerald-400 font-bold">{inv.fdn}</td>
                                <td className="p-2">{inv.seller_name}</td>
                                <td className="p-2 text-neutral-400">{inv.buyer_name || 'Walk-in'}</td>
                                <td className="p-2 font-semibold">UGX {Number(inv.gross_amount).toLocaleString()}</td>
                                <td className="p-2 text-neutral-400">UGX {Number(inv.tax_amount).toLocaleString()}</td>
                                <td className="p-2">
                                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px]">
                                    {inv.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Stamps table if DTS */}
                  {Array.isArray(inspectingData.stamps) && (
                    <div>
                      <h4 className="text-xs font-semibold text-neutral-300 mb-2 font-sans flex items-center gap-1.5">
                        <StampIcon size={14} />
                        Affixed Digital Tax Stamps (dts_stamps)
                      </h4>
                      <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-neutral-950">
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="bg-neutral-900 border-b border-neutral-800 text-[10px] text-neutral-400">
                              <th className="p-2">Stamp Serial</th>
                              <th className="p-2">Category</th>
                              <th className="p-2">Brand Name</th>
                              <th className="p-2">Manufacturer</th>
                              <th className="p-2">Production Date</th>
                              <th className="p-2">Kakasa Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(inspectingData.stamps as StampRow[]).map((st) => (
                              <tr key={st.stamp_code} className="border-b border-neutral-900 hover:bg-neutral-900/50">
                                <td className="p-2 text-amber-400 font-bold">{st.stamp_code}</td>
                                <td className="p-2">{st.product_category}</td>
                                <td className="p-2">{st.brand_name}</td>
                                <td className="p-2 text-neutral-400">{st.manufacturer_name}</td>
                                <td className="p-2 text-neutral-400">{st.production_date}</td>
                                <td className="p-2">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[10px] ${
                                      st.status === 'GENUINE'
                                        ? 'bg-emerald-500/20 text-emerald-300'
                                        : 'bg-red-500/20 text-red-300'
                                    }`}
                                  >
                                    {st.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Entities table if URSB */}
                  {Array.isArray(inspectingData.entities) && (
                    <div>
                      <h4 className="text-xs font-semibold text-neutral-300 mb-2 font-sans flex items-center gap-1.5">
                        <FileIcon size={14} />
                        Incorporated Entities &amp; Business Names (ursb_entities)
                      </h4>
                      <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-neutral-950">
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="bg-neutral-900 border-b border-neutral-800 text-[10px] text-neutral-400">
                              <th className="p-2">Registration No</th>
                              <th className="p-2">Business Name</th>
                              <th className="p-2">Type</th>
                              <th className="p-2">District</th>
                              <th className="p-2">Registration Date</th>
                              <th className="p-2">Legal Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(inspectingData.entities as EntityRow[]).map((e) => (
                              <tr key={e.registration_number} className="border-b border-neutral-900 hover:bg-neutral-900/50">
                                <td className="p-2 text-purple-400 font-bold">{e.registration_number}</td>
                                <td className="p-2 font-semibold text-white">{e.business_name}</td>
                                <td className="p-2 text-neutral-400">{e.entity_type}</td>
                                <td className="p-2">{e.district}</td>
                                <td className="p-2 text-neutral-400">{e.registration_date}</td>
                                <td className="p-2">
                                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px]">
                                    {e.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Consignments table if BWIMS */}
                  {Array.isArray(inspectingData.consignments) && (
                    <div>
                      <h4 className="text-xs font-semibold text-neutral-300 mb-2 font-sans flex items-center gap-1.5">
                        <DatabaseIcon size={14} />
                        Customs Bonded Consignments IM7 (bwims_consignments)
                      </h4>
                      <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-neutral-950">
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="bg-neutral-900 border-b border-neutral-800 text-[10px] text-neutral-400">
                              <th className="p-2">IM7 Entry</th>
                              <th className="p-2">Importer</th>
                              <th className="p-2">Warehouse</th>
                              <th className="p-2">Goods Description</th>
                              <th className="p-2">CIF Value (UGX)</th>
                              <th className="p-2">Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(inspectingData.consignments as ConsignmentRow[]).map((c) => (
                              <tr key={c.entry_number} className="border-b border-neutral-900 hover:bg-neutral-900/50">
                                <td className="p-2 text-emerald-400 font-bold">{c.entry_number}</td>
                                <td className="p-2">{c.importer_name}</td>
                                <td className="p-2 text-neutral-400">{c.warehouse_code}</td>
                                <td className="p-2 text-white">{c.goods_description}</td>
                                <td className="p-2 font-semibold">UGX {Number(c.cif_value_ugx).toLocaleString()}</td>
                                <td className="p-2">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[10px] ${
                                      c.status === 'OVERSTAYED_AUCTION_RISK'
                                        ? 'bg-red-500/20 text-red-300'
                                        : 'bg-emerald-500/20 text-emerald-300'
                                    }`}
                                  >
                                    {c.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Taxpayers table if TIN */}
                  {Array.isArray(inspectingData.taxpayers) && (
                    <div>
                      <h4 className="text-xs font-semibold text-neutral-300 mb-2 font-sans flex items-center gap-1.5">
                        <ShieldCheckIcon size={14} />
                        Registered Taxpayers &amp; Obligations (tin_taxpayers)
                      </h4>
                      <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-neutral-950">
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="bg-neutral-900 border-b border-neutral-800 text-[10px] text-neutral-400">
                              <th className="p-2">10-Digit TIN</th>
                              <th className="p-2">Legal Name</th>
                              <th className="p-2">Category</th>
                              <th className="p-2">District</th>
                              <th className="p-2">Registered Date</th>
                              <th className="p-2">Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(inspectingData.taxpayers as TaxpayerRow[]).map((tp) => (
                              <tr key={tp.tin} className="border-b border-neutral-900 hover:bg-neutral-900/50">
                                <td className="p-2 text-cyan-400 font-bold">{tp.tin}</td>
                                <td className="p-2 font-semibold text-white">{tp.legal_name}</td>
                                <td className="p-2 text-neutral-400">{tp.category}</td>
                                <td className="p-2">{tp.district}</td>
                                <td className="p-2 text-neutral-400">{tp.registration_date}</td>
                                <td className="p-2">
                                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px]">
                                    {tp.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* PRNs table if Payment System */}
                  {Array.isArray(inspectingData.prns) && (
                    <div>
                      <h4 className="text-xs font-semibold text-neutral-300 mb-2 font-sans flex items-center gap-1.5">
                        <PlugIcon size={14} />
                        Payment Registration Numbers &amp; PRN Vouchers (payment_prns)
                      </h4>
                      <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-neutral-950">
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="bg-neutral-900 border-b border-neutral-800 text-[10px] text-neutral-400">
                              <th className="p-2">12-Digit PRN</th>
                              <th className="p-2">Taxpayer / Payer</th>
                              <th className="p-2">Tax Head / Fee</th>
                              <th className="p-2">Amount (UGX)</th>
                              <th className="p-2">Expiry Date</th>
                              <th className="p-2">PRN Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(inspectingData.prns as PrnRow[]).map((p) => (
                              <tr key={p.prn} className="border-b border-neutral-900 hover:bg-neutral-900/50">
                                <td className="p-2 text-rose-400 font-bold">{p.prn}</td>
                                <td className="p-2 font-semibold text-white">{p.taxpayer_name}</td>
                                <td className="p-2 text-neutral-400">{p.tax_head}</td>
                                <td className="p-2 font-semibold">UGX {Number(p.amount_ugx).toLocaleString()}</td>
                                <td className="p-2 text-neutral-400">{p.expiry_date}</td>
                                <td className="p-2">
                                  <span
                                    className={`px-1.5 py-0.5 rounded text-[10px] ${
                                      p.status === 'CLEARED'
                                        ? 'bg-emerald-500/20 text-emerald-300'
                                        : p.status === 'EXPIRED'
                                        ? 'bg-red-500/20 text-red-300'
                                        : 'bg-amber-500/20 text-amber-300'
                                    }`}
                                  >
                                    {p.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center py-12 text-neutral-500 text-xs">
                  Loading database records...
                </div>
              )}
            </div>
          ) : (
            /* External API / MCP Registration Tab */
            <form onSubmit={handleRegisterExternal} className="max-w-xl mx-auto space-y-4 py-4">
              <div>
                <h3 className="text-sm font-semibold text-white mb-1">Connect External API or Remote MCP Server</h3>
                <p className="text-xs text-neutral-400 mb-4">
                  Connect live third-party or containerized enterprise services (like Stripe, GitHub, or standalone URA microservices) using standard MCP 2026 endpoints.
                </p>
              </div>

              {extStatus && (
                <div
                  className={`p-3 rounded-lg text-xs font-medium ${
                    extStatus.includes('Successfully')
                      ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                      : 'bg-red-500/15 text-red-400 border border-red-500/30'
                  }`}
                >
                  {extStatus}
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  Connector System Identifier *
                </label>
                <input
                  type="text"
                  required
                  placeholder="e.g. stripe_payments, github_repo, ura_remote_node"
                  className="w-full px-3 py-2 bg-neutral-800 border border-neutral-700 rounded-lg text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-emerald-500 font-mono"
                  value={extName}
                  onChange={(e) => setExtName(e.target.value)}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  Service Endpoint / Base URL *
                </label>
                <input
                  type="url"
                  required
                  placeholder="e.g. http://localhost:8200 or https://mcp.external.org"
                  className="w-full px-3 py-2 bg-neutral-800 border border-neutral-700 rounded-lg text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-emerald-500 font-mono"
                  value={extUrl}
                  onChange={(e) => setExtUrl(e.target.value)}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  Bearer API Token / Secret (Optional)
                </label>
                <input
                  type="password"
                  placeholder="Bearer token or leave empty if public / sandbox"
                  className="w-full px-3 py-2 bg-neutral-800 border border-neutral-700 rounded-lg text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-emerald-500 font-mono"
                  value={extKey}
                  onChange={(e) => setExtKey(e.target.value)}
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  System Architecture Category
                </label>
                <select
                  className="w-full px-3 py-2 bg-neutral-800 border border-neutral-700 rounded-lg text-xs text-white focus:outline-none focus:border-emerald-500"
                  value={extType}
                  onChange={(e) => setExtType(e.target.value)}
                >
                  <option value="external_mcp">Remote MCP 2026 Server (/mcp/call)</option>
                  <option value="payment_gateway">Payment Gateway (e.g. Stripe, PayPal)</option>
                  <option value="customs_warehousing">Customs Logistics &amp; Freight</option>
                  <option value="business_registry">URSB / Corporate Registry</option>
                </select>
              </div>

              <div className="pt-2">
                <button
                  type="submit"
                  disabled={isRegistering}
                  className="w-full py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 font-semibold text-xs text-white transition shadow-md disabled:opacity-50"
                >
                  {isRegistering ? 'Probing & Registering Connector...' : 'Connect & Wire into Agentic System'}
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-3 border-t border-neutral-800 bg-neutral-900/80 text-xs text-neutral-400">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
            <span>Connected Systems: <strong>{activeConnectorIds.length} of {connectors.length} active</strong></span>
          </div>
          <button
            onClick={closeModal}
            className="px-4 py-1.5 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-white font-medium transition"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
