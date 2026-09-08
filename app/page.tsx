'use client';

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Archive,
  BookOpenText,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Database,
  Download,
  ExternalLink,
  FileSearch,
  FileText,
  FolderOpen,
  LibraryBig,
  LoaderCircle,
  Menu,
  MessageSquareText,
  PanelRightClose,
  Plus,
  RefreshCw,
  Search,
  Send,
  Server,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';

type PackageItem = {
  id: number;
  filename: string;
  title: string;
  product: string;
  version: string;
  size_bytes: number;
  entry_count: number;
  indexed_count: number;
  status: 'queued' | 'indexing' | 'ready' | 'error';
  error?: string;
  updated_at: string;
  vendor: string;
  vendor_slug: string;
  source_path: string;
  first_entry?: string;
};

type PageItem = {
  id: number;
  entry_name: string;
  title: string;
  docno: string;
  revision: string;
  snippet?: string;
};

type SearchResult = {
  page_id: number;
  package_id: number;
  entry_name: string;
  title: string;
  docno: string;
  revision: string;
  package_title: string;
  product: string;
  vendor: string;
  snippet: string;
};

type SourceItem = {
  id: number;
  path: string;
  vendor: string;
  vendor_slug: string;
  package_count: number;
};

type LibraryStatus = {
  packages: number;
  ready: number;
  indexing: number;
  queued: number;
  indexed_pages: number;
  total_pages: number;
};

type Citation = {
  page_id: number;
  package_id: number;
  entry_name: string;
  title: string;
  docno: string;
  package_title: string;
};

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  mode?: string;
};

type AiSettings = {
  provider: 'ollama' | 'openai' | 'gemini' | 'anthropic' | 'openai_compatible';
  model: string;
  base_url: string;
  provider_name?: string;
  requires_api_key?: boolean;
  api_key_available?: boolean;
  credential_url?: string;
};

type SourceVendor = 'ericsson' | 'zte';

const AI_PRESETS: Record<AiSettings['provider'], Pick<AiSettings, 'model' | 'base_url' | 'provider_name' | 'requires_api_key' | 'credential_url'>> = {
  ollama: { model: 'qwen2.5:7b', base_url: 'http://127.0.0.1:11434', provider_name: 'Ollama local', requires_api_key: false, credential_url: 'https://ollama.com/download' },
  openai: { model: 'gpt-5-mini', base_url: 'https://api.openai.com/v1', provider_name: 'OpenAI', requires_api_key: true, credential_url: 'https://platform.openai.com/api-keys' },
  gemini: { model: 'gemini-2.5-flash', base_url: 'https://generativelanguage.googleapis.com/v1beta', provider_name: 'Google Gemini', requires_api_key: true, credential_url: 'https://aistudio.google.com/apikey' },
  anthropic: { model: 'claude-sonnet-4-5-20250929', base_url: 'https://api.anthropic.com/v1', provider_name: 'Anthropic Claude', requires_api_key: true, credential_url: 'https://console.anthropic.com/settings/keys' },
  openai_compatible: { model: 'local-model', base_url: 'http://127.0.0.1:1234/v1', provider_name: 'OpenAI-compatible', requires_api_key: false, credential_url: '' },
};

const SOURCE_DEFAULTS: Record<SourceVendor, string> = {
  ericsson: 'F:\\Library\\Ericsson_Alex',
  zte: 'F:\\Library\\ZTE_Alex',
};

const DEV_API = 'http://127.0.0.1:8765';

function apiBase() {
  if (typeof window === 'undefined') return DEV_API;
  return window.location.port === '8765' ? '' : DEV_API;
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (options?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${apiBase()}${path}`, {
    ...options,
    headers,
  });
  const data = await response.json() as { error?: string } | T;
  if (!response.ok) {
    const error = data as { error?: string };
    throw new Error(error.error || 'Không thể kết nối DocAtlas.');
  }
  return data as T;
}

function formatBytes(value: number) {
  if (!value) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function encodeEntry(value: string) {
  return value.split('/').map(encodeURIComponent).join('/');
}

function cleanSnippet(value: string) {
  return value.replace(/<\/?mark>/gi, '').replace(/\s+/g, ' ').trim();
}

function HighlightText({ value }: { value: string }) {
  const parts = value.split(/(<mark>.*?<\/mark>)/gi);
  return (
    <>
      {parts.map((part, index) =>
        /^<mark>/i.test(part) ? (
          <mark key={`${part}-${index}`}>{part.replace(/<\/?mark>/gi, '')}</mark>
        ) : (
          <span key={`${part}-${index}`}>{part}</span>
        ),
      )}
    </>
  );
}

export default function Home() {
  const searchRef = useRef<HTMLInputElement>(null);
  const [packages, setPackages] = useState<PackageItem[]>([]);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [status, setStatus] = useState<LibraryStatus | null>(null);
  const [pages, setPages] = useState<PageItem[]>([]);
  const [selectedPackageId, setSelectedPackageId] = useState<number | null>(null);
  const [selectedPage, setSelectedPage] = useState<PageItem | null>(null);
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [mainTab, setMainTab] = useState('reader');
  const [sourceDialog, setSourceDialog] = useState(false);
  const [settingsDialog, setSettingsDialog] = useState(false);
  const [sourceVendor, setSourceVendor] = useState<SourceVendor>('ericsson');
  const [sourcePath, setSourcePath] = useState('F:\\Library\\Ericsson_Alex');
  const [busyAction, setBusyAction] = useState(false);
  const [notice, setNotice] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<PackageItem | null>(null);
  const [prompt, setPrompt] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [asking, setAsking] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [testingAi, setTestingAi] = useState(false);
  const [aiOpen, setAiOpen] = useState(true);
  const [settings, setSettings] = useState<AiSettings>({
    provider: 'ollama',
    model: 'qwen2.5:7b',
    base_url: 'http://127.0.0.1:11434',
  });

  const selectedPackage = packages.find((item) => item.id === selectedPackageId) ?? null;
  const vendorGroups = useMemo(() => {
    return ['ericsson', 'zte'].map((slug) => {
      const vendorPackages = packages.filter((item) => item.vendor_slug === slug);
      const products = new Map<string, number>();
      vendorPackages.forEach((item) => products.set(item.product, (products.get(item.product) ?? 0) + 1));
      return {
        slug,
        name: slug === 'ericsson' ? 'Ericsson' : 'ZTE',
        format: slug === 'ericsson' ? 'Alex (.alx)' : 'eReader (.zed)',
        packages: vendorPackages,
        products: [...products.entries()],
      };
    });
  }, [packages]);

  const refreshLibrary = useCallback(async () => {
    try {
      const [packageData, sourceData, statusData] = await Promise.all([
        api<PackageItem[]>('/api/packages'),
        api<SourceItem[]>('/api/sources'),
        api<LibraryStatus>('/api/status'),
      ]);
      setPackages(packageData);
      setSources(sourceData);
      setStatus(statusData);
      setSelectedPackageId((current) => {
        if (current && packageData.some((item) => item.id === current)) return current;
        const firstReady = packageData.find((item) => item.status === 'ready');
        return firstReady?.id ?? packageData[0]?.id ?? null;
      });
    } catch {
      setNotice('Máy chủ thư viện chưa chạy. Mở “Start DocAtlas” để kết nối dữ liệu thật.');
    }
  }, []);

  useEffect(() => {
    void refreshLibrary();
    void api<AiSettings>('/api/settings').then(setSettings).catch(() => undefined);
  }, [refreshLibrary]);

  useEffect(() => {
    if (!status || (!status.indexing && !status.queued)) return;
    const timer = window.setInterval(() => void refreshLibrary(), 2500);
    return () => window.clearInterval(timer);
  }, [status, refreshLibrary]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  useEffect(() => {
    if (window.matchMedia('(max-width: 960px)').matches) setAiOpen(false);
  }, []);

  useEffect(() => {
    if (!selectedPackageId) {
      setPages([]);
      setSelectedPage(null);
      return;
    }
    let active = true;
    void api<PageItem[]>(`/api/packages/${selectedPackageId}/pages`)
      .then((items) => {
        if (!active) return;
        setPages(items);
        setSelectedPage((current) => {
          if (current && items.some((item) => item.entry_name === current.entry_name)) return current;
          return items[0] ?? null;
        });
      })
      .catch(() => {
        if (active) {
          setPages([]);
          setSelectedPage(null);
        }
      });
    return () => { active = false; };
  }, [selectedPackageId]);

  useEffect(() => {
    const normalized = query.trim();
    if (normalized.length < 2) {
      setSearchResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(() => {
      void api<SearchResult[]>(`/api/search?q=${encodeURIComponent(normalized)}&limit=50`)
        .then(setSearchResults)
        .catch(() => setSearchResults([]))
        .finally(() => setSearching(false));
    }, 260);
    return () => window.clearTimeout(timer);
  }, [query]);

  const openPage = useCallback((packageId: number, page: PageItem | SearchResult) => {
    setSelectedPackageId(packageId);
    setSelectedPage({
      id: 'id' in page ? page.id : page.page_id,
      entry_name: page.entry_name,
      title: page.title,
      docno: page.docno,
      revision: page.revision,
      snippet: page.snippet,
    });
    setMainTab('reader');
    setQuery('');
  }, []);

  useEffect(() => {
    const modelContext = (document as Document & {
      modelContext?: {
        registerTool: (tool: unknown, options?: { signal?: AbortSignal }) => void | Promise<void>;
      };
    }).modelContext;
    if (!modelContext?.registerTool) return;
    const lifecycle = new AbortController();
    try {
      void Promise.resolve(modelContext.registerTool({
        name: 'search_documents',
        title: 'Tìm trong thư viện DocAtlas',
        description: 'Tìm nội dung trong các tài liệu kỹ thuật đã lập chỉ mục.',
        inputSchema: {
          type: 'object',
          properties: { query: { type: 'string', minLength: 2 } },
          required: ['query'],
          additionalProperties: false,
        },
        annotations: { readOnlyHint: true, untrustedContentHint: true },
        async execute(input: unknown) {
          const value = input as { query?: string };
          if (!value.query || value.query.trim().length < 2) throw new Error('query phải có ít nhất 2 ký tự');
          const results = await api<SearchResult[]>(`/api/search?q=${encodeURIComponent(value.query)}&limit=10`);
          return results.map((item) => ({
            packageId: item.package_id,
            pageId: item.page_id,
            title: item.title,
            documentNumber: item.docno,
            excerpt: cleanSnippet(item.snippet),
          }));
        },
      }, { signal: lifecycle.signal })).catch(() => undefined);
    } catch {
      // WebMCP is optional in browsers that do not support it yet.
    }
    return () => lifecycle.abort();
  }, []);

  async function addSource(event: FormEvent) {
    event.preventDefault();
    setBusyAction(true);
    setNotice('');
    try {
      const result = await api<{ found: number; queued: number }>('/api/sources', {
        method: 'POST',
        body: JSON.stringify({ path: sourcePath, vendor: sourceVendor }),
      });
      setNotice(`Đã tìm thấy ${result.found} gói ${sourceVendor === 'ericsson' ? 'ALX' : 'ZED'}; ${result.queued} gói được đưa vào hàng đợi lập chỉ mục.`);
      setSourceDialog(false);
      await refreshLibrary();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Không thể thêm nguồn.');
    } finally {
      setBusyAction(false);
    }
  }

  async function rescanAll() {
    if (!sources.length) return;
    setBusyAction(true);
    try {
      let found = 0;
      let queued = 0;
      for (const source of sources) {
        const result = await api<{ found: number; queued: number }>('/api/scan', {
          method: 'POST',
          body: JSON.stringify({ source_id: source.id, force: false }),
        });
        found += result.found;
        queued += result.queued;
      }
      setNotice(`Đã quét ${found} gói từ ${sources.length} nguồn; ${queued} gói đang chờ lập chỉ mục.`);
      await refreshLibrary();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Không thể quét thư viện.');
    } finally {
      setBusyAction(false);
    }
  }

  async function deletePackage() {
    if (!deleteTarget) return;
    setBusyAction(true);
    try {
      await api(`/api/packages/${deleteTarget.id}`, { method: 'DELETE' });
      setNotice('Đã bỏ gói khỏi danh mục. File gốc trên ổ đĩa vẫn được giữ nguyên.');
      setDeleteTarget(null);
      await refreshLibrary();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Không thể bỏ gói.');
    } finally {
      setBusyAction(false);
    }
  }

  async function saveAiSettings(event: FormEvent) {
    event.preventDefault();
    setBusyAction(true);
    try {
      const saved = await api<AiSettings>('/api/settings', {
        method: 'POST',
        body: JSON.stringify({ ...settings, api_key: apiKey }),
      });
      setSettings(saved);
      setApiKey('');
      setSettingsDialog(false);
      if (saved.api_key_available || !saved.requires_api_key) {
        try {
          const result = await api<{ message: string }>('/api/settings/test', {
            method: 'POST',
            body: JSON.stringify({}),
          });
          setNotice(result.message);
        } catch (testError) {
          setNotice(`Đã lưu cấu hình, nhưng kiểm tra chưa thành công: ${testError instanceof Error ? testError.message : 'không thể kết nối'}`);
        }
      } else {
        setNotice('Đã lưu cấu hình; cần thêm API key để dùng nhà cung cấp này.');
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Không thể lưu cấu hình AI.');
    } finally {
      setBusyAction(false);
    }
  }

  async function testAiConnection() {
    setTestingAi(true);
    setNotice('');
    try {
      const result = await api<{ message: string }>('/api/settings/test', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setNotice(result.message);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Không thể kiểm tra kết nối AI.');
    } finally {
      setTestingAi(false);
    }
  }

  async function clearAiCredential() {
    setBusyAction(true);
    try {
      const saved = await api<AiSettings>('/api/settings', {
        method: 'POST',
        body: JSON.stringify({ ...settings, clear_api_key: true }),
      });
      setSettings(saved);
      setApiKey('');
      setNotice('Đã ngắt khóa API khỏi DocAtlas.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Không thể ngắt kết nối AI.');
    } finally {
      setBusyAction(false);
    }
  }

  function changeAiProvider(provider: AiSettings['provider']) {
    setApiKey('');
    setSettings({
      provider,
      ...AI_PRESETS[provider],
      api_key_available: false,
    });
  }

  function changeSourceVendor(vendor: SourceVendor) {
    setSourceVendor(vendor);
    setSourcePath(SOURCE_DEFAULTS[vendor]);
  }

  async function askAi(event: FormEvent) {
    event.preventDefault();
    const question = prompt.trim();
    if (!question || asking) return;
    setPrompt('');
    setMessages((current) => [...current, { role: 'user', content: question }]);
    setAsking(true);
    try {
      const response = await api<{ answer: string; citations: Citation[]; mode: string }>('/api/ai/chat', {
        method: 'POST',
        body: JSON.stringify({ question, package_id: selectedPackageId }),
      });
      setMessages((current) => [...current, {
        role: 'assistant',
        content: response.answer,
        citations: response.citations,
        mode: response.mode,
      }]);
    } catch (error) {
      setMessages((current) => [...current, {
        role: 'assistant',
        content: error instanceof Error ? error.message : 'Không thể xử lý câu hỏi.',
        mode: 'error',
      }]);
    } finally {
      setAsking(false);
    }
  }

  const totalProgress = status?.total_pages
    ? Math.min(100, Math.round((status.indexed_pages / status.total_pages) * 100))
    : 0;
  const iframeSource = selectedPackage && selectedPage && pages.some((page) => page.entry_name === selectedPage.entry_name)
    ? `${apiBase()}/api/packages/${selectedPackage.id}/files/${encodeEntry(selectedPage.entry_name)}`
    : '';

  return (
    <main className={`app-shell ${aiOpen ? '' : 'ai-collapsed'}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <Button className="mobile-menu" variant="ghost" size="icon" aria-label="Mở thư viện">
            <Menu />
          </Button>
          <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
          <div>
            <div className="brand-name">DocAtlas</div>
            <div className="brand-subtitle">Knowledge workspace</div>
          </div>
          <Badge className="local-badge" variant="secondary"><span className="status-dot" /> Local</Badge>
        </div>

        <label className="global-search">
          <Search aria-hidden="true" />
          <Input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm lệnh, thủ tục, mã tài liệu…"
            aria-label="Tìm trong thư viện"
          />
          {query ? (
            <button onClick={() => setQuery('')} aria-label="Xóa tìm kiếm"><X /></button>
          ) : (
            <kbd>Ctrl K</kbd>
          )}
        </label>

        <div className="top-actions">
          <div className="index-state">
            {status?.indexing || status?.queued ? <LoaderCircle className="spin" /> : <CircleCheck />}
            <span>
              <strong>{status?.indexing || status?.queued ? 'Đang lập chỉ mục' : 'Thư viện sẵn sàng'}</strong>
              <small>{status?.packages ?? 0} gói · {status?.indexed_pages ?? 0} tài liệu</small>
            </span>
          </div>
          <Button variant="ghost" size="icon" aria-label="Cài đặt AI" onClick={() => setSettingsDialog(true)}>
            <Settings2 />
          </Button>
          <div className="avatar">NT</div>
        </div>
      </header>

      <div className="workspace-grid">
        <aside className="library-panel">
          <div className="panel-heading">
            <span>Thư viện</span>
            <Button variant="ghost" size="icon" aria-label="Thêm nguồn" onClick={() => setSourceDialog(true)}>
              <Plus />
            </Button>
          </div>

          <ScrollArea className="library-scroll">
            <nav aria-label="Nguồn tài liệu">
              <button className="nav-row active" onClick={() => setMainTab('documents')}>
                <LibraryBig /><span>Tất cả tài liệu</span><small>{packages.length}</small>
              </button>
              <button className="nav-row" onClick={() => setAiOpen(true)}>
                <MessageSquareText /><span>Trao đổi với AI</span><small>{messages.length}</small>
              </button>

              <p className="nav-label">Vendor</p>
              {vendorGroups.map((group) => (
                <div key={group.slug} className="vendor-block">
                  <button className="nav-row vendor-row active-soft" onClick={() => setMainTab('documents')}>
                    <ChevronDown /><span className={`vendor-glyph ${group.slug}`}>{group.name.charAt(0)}</span>
                    <span><strong>{group.name}</strong><small>{group.format}</small></span>
                    <Badge variant="secondary">{group.packages.length}</Badge>
                  </button>
                  {group.products.length > 0 && (
                    <div className="tree-block">
                      {group.products.map(([product, count]) => (
                        <button key={`${group.slug}-${product}`} className="tree-row" onClick={() => setMainTab('documents')}>
                          <ChevronRight /><FolderOpen /><span>{product}</span><small>{count}</small>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}

              <p className="nav-label">Bộ sưu tập</p>
              <button className="nav-row"><Archive /><span>Tài liệu hay dùng</span><small>—</small></button>
            </nav>
          </ScrollArea>

          {status && (status.indexing > 0 || status.queued > 0) && (
            <div className="index-progress-card">
              <div><span>Đang xử lý thư viện</span><strong>{totalProgress}%</strong></div>
              <Progress value={totalProgress} />
              <small>{status.ready} sẵn sàng · {status.queued + status.indexing} đang chờ</small>
            </div>
          )}

          <div className="storage-card">
            <Server />
            <div>
              <strong>Lưu trên máy</strong>
              <span>{sources.length ? `${sources.length} nguồn đã kết nối` : 'Chưa kết nối thư mục'}</span>
            </div>
            <Button variant="ghost" size="icon" aria-label="Quét lại tất cả nguồn" disabled={!sources.length || busyAction} onClick={() => void rescanAll()}>
              <RefreshCw className={busyAction ? 'spin' : ''} />
            </Button>
          </div>
        </aside>

        <Tabs value={mainTab} onValueChange={setMainTab} className="document-panel">
          <TabsList variant="line" className="doc-tabs">
            <TabsTrigger value="reader"><BookOpenText /> Đang đọc</TabsTrigger>
            <TabsTrigger value="documents"><FileText /> Tài liệu</TabsTrigger>
          </TabsList>

          {notice && (
            <div className="notice-bar"><AlertCircle /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="Đóng thông báo"><X /></button></div>
          )}

          {query.trim().length >= 2 ? (
            <section className="search-results" aria-live="polite">
              <div className="result-heading">
                <div><span>Kết quả tìm kiếm</span><h1>“{query}”</h1></div>
                <Badge variant="secondary">{searching ? 'Đang tìm…' : `${searchResults.length} kết quả`}</Badge>
              </div>
              <div className="result-list">
                {searchResults.map((result) => (
                  <button key={`${result.package_id}-${result.page_id}`} onClick={() => openPage(result.package_id, result)} className="result-card">
                    <span className="result-icon"><FileText /></span>
                    <span>
                      <strong>{result.title}</strong>
                      <small>{result.vendor} · {result.product}{result.docno ? ` · ${result.docno}` : ''}</small>
                      <p><HighlightText value={result.snippet} /></p>
                    </span>
                    <ChevronRight />
                  </button>
                ))}
                {!searching && searchResults.length === 0 && (
                  <div className="empty-results"><Search /><strong>Chưa tìm thấy kết quả</strong><span>Có thể gói tài liệu vẫn đang được lập chỉ mục.</span></div>
                )}
              </div>
            </section>
          ) : (
            <>
              <TabsContent value="reader" className="reader-tab-content">
                <div className="document-toolbar">
                  <div className="breadcrumbs">
                    <span>{selectedPackage?.vendor ?? 'Thư viện'}</span><ChevronRight />
                    <span>{selectedPackage?.title ?? 'Chưa chọn gói'}</span><ChevronRight />
                    <strong>{selectedPage?.title ?? 'Chưa chọn tài liệu'}</strong>
                  </div>
                  <div className="document-tools">
                    <Button variant="outline" size="sm" onClick={() => searchRef.current?.focus()}><Search /> Tìm trong thư viện</Button>
                    {selectedPackage && (
                      <a className="download-button" href={`${apiBase()}/api/packages/${selectedPackage.id}/download`} download={selectedPackage.filename}>
                        <Download /> Tải {selectedPackage.vendor_slug === 'zte' ? 'ZED' : 'ALX'}
                      </a>
                    )}
                    {!aiOpen && <Button variant="outline" size="sm" onClick={() => setAiOpen(true)}><Bot /> Mở AI</Button>}
                  </div>
                </div>

                <div className="reader-area">
                  <aside className="outline-panel">
                    <div className="outline-title">Trong gói ({pages.length})</div>
                    <ScrollArea className="outline-scroll">
                      {pages.slice(0, 500).map((page) => (
                        <button key={page.id} className={selectedPage?.entry_name === page.entry_name ? 'active' : ''} onClick={() => setSelectedPage(page)} title={page.title}>
                          {page.title}
                        </button>
                      ))}
                    </ScrollArea>
                  </aside>

                  <div className="reader-scroll">
                    {iframeSource ? (
                      <iframe
                        key={iframeSource}
                        title={selectedPage?.title || 'Tài liệu kỹ thuật'}
                        src={iframeSource}
                        className="document-frame"
                        sandbox="allow-same-origin allow-downloads"
                      />
                    ) : (
                      <div className="reader-empty">
                        {selectedPackage?.status === 'indexing' || selectedPackage?.status === 'queued' ? <LoaderCircle className="spin" /> : <FileSearch />}
                        <h1>{selectedPackage ? 'Gói đang được chuẩn bị' : 'Chọn một gói tài liệu'}</h1>
                        <p>{selectedPackage ? 'DocAtlas đang đọc cấu trúc gói và tạo chỉ mục. Mày vẫn có thể mở tab Tài liệu để theo dõi.' : 'Thư viện sẽ hiện ở đây sau khi kết nối thư mục tài liệu.'}</p>
                        <Button variant="outline" onClick={() => setMainTab('documents')}>Mở danh mục tài liệu</Button>
                      </div>
                    )}
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="documents" className="documents-tab-content">
                <section className="documents-view">
                  <div className="documents-heading">
                    <div><span>Quản lý thư viện</span><h1>Ericsson Alex & ZTE eReader</h1><p>Thêm, quét lại và mở các gói ALX/ZED mà không thay đổi file gốc.</p></div>
                    <div>
                      <Button variant="outline" disabled={!sources.length || busyAction} onClick={() => void rescanAll()}><RefreshCw className={busyAction ? 'spin' : ''} /> Quét lại tất cả</Button>
                      <Button onClick={() => setSourceDialog(true)}><Plus /> Thêm nguồn</Button>
                    </div>
                  </div>

                  <div className="library-stats">
                    <div><Database /><span><strong>{packages.length}</strong><small>Gói ALX / ZED</small></span></div>
                    <div><CircleCheck /><span><strong>{status?.ready ?? 0}</strong><small>Đã sẵn sàng</small></span></div>
                    <div><FileText /><span><strong>{status?.indexed_pages ?? 0}</strong><small>Tài liệu đã đọc</small></span></div>
                  </div>

                  <div className="package-table-wrap">
                    <table className="package-table">
                      <thead><tr><th>Tên gói</th><th>Vendor</th><th>Nhóm</th><th>Dung lượng</th><th>Tiến độ</th><th>Trạng thái</th><th><span className="sr-only">Thao tác</span></th></tr></thead>
                      <tbody>
                        {packages.map((item) => {
                          const progress = item.entry_count ? Math.min(100, Math.round((item.indexed_count / item.entry_count) * 100)) : 0;
                          return (
                            <tr key={item.id}>
                              <td><button className="package-name" onClick={() => { setSelectedPackageId(item.id); setMainTab('reader'); }}><FileText /><span><strong>{item.title}</strong><small>{item.filename}</small></span></button></td>
                              <td><Badge variant="outline">{item.vendor}</Badge></td>
                              <td><Badge variant="secondary">{item.product}</Badge></td>
                              <td>{formatBytes(item.size_bytes)}</td>
                              <td><div className="row-progress"><Progress value={progress} /><span>{progress}%</span></div></td>
                              <td><span className={`status-pill ${item.status}`}>{item.status === 'ready' ? <Check /> : item.status === 'error' ? <AlertCircle /> : <LoaderCircle className="spin" />}{item.status === 'ready' ? 'Sẵn sàng' : item.status === 'indexing' ? 'Đang đọc' : item.status === 'queued' ? 'Đang chờ' : 'Có lỗi'}</span></td>
                              <td><Button variant="ghost" size="icon" aria-label={`Bỏ ${item.title} khỏi danh mục`} onClick={() => setDeleteTarget(item)}><Trash2 /></Button></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {!packages.length && <div className="table-empty"><FolderOpen /><strong>Chưa có gói tài liệu</strong><span>Thêm thư mục Ericsson hoặc ZTE để bắt đầu.</span></div>}
                  </div>
                </section>
              </TabsContent>
            </>
          )}
        </Tabs>

        {aiOpen && (
          <aside className="ai-panel">
            <div className="ai-heading">
              <div><span className="ai-icon"><Bot /></span><div><strong>AI tài liệu</strong><small><span className="status-dot" /> {settings.provider_name || (settings.provider === 'ollama' ? 'Ollama local' : settings.model)}</small></div></div>
              <Button variant="ghost" size="icon" aria-label="Đóng bảng AI" onClick={() => setAiOpen(false)}><PanelRightClose /></Button>
            </div>

            <div className="scope-card">
              <span>Phạm vi trả lời</span>
              <button onClick={() => setMainTab('documents')}><FileText /><span><strong>{selectedPackage?.title ?? 'Toàn bộ thư viện'}</strong><small>{selectedPage?.docno || selectedPackage?.product || 'Mọi tài liệu đã lập chỉ mục'}</small></span><ChevronDown /></button>
            </div>

            <ScrollArea className="chat-scroll">
              {messages.length === 0 ? (
                <>
                  <div className="ai-welcome"><div className="spark-orb"><Sparkles /></div><h2>Hỏi trực tiếp tài liệu</h2><p>Câu trả lời được giới hạn trong nội dung đã lập chỉ mục và kèm nguồn để kiểm tra.</p></div>
                  <div className="suggestions">
                    {['Tóm tắt tài liệu này trong 5 ý chính', 'Các bước xử lý alarm là gì?', 'Liệt kê role cần thiết và chức năng'].map((suggestion) => (
                      <button key={suggestion} onClick={() => setPrompt(suggestion)}>{suggestion}</button>
                    ))}
                  </div>
                </>
              ) : (
                <div className="messages">
                  {messages.map((message, index) => (
                    <div key={`${message.role}-${index}`} className={`message ${message.role}`}>
                      <span>{message.role === 'assistant' ? <Sparkles /> : 'Mày'}</span>
                      <p>{message.content}</p>
                      {message.citations && message.citations.length > 0 && (
                        <div className="citations">
                          {message.citations.map((citation, citationIndex) => (
                            <button key={`${citation.page_id}-${citationIndex}`} onClick={() => openPage(citation.package_id, { id: citation.page_id, entry_name: citation.entry_name, title: citation.title, docno: citation.docno, revision: '' })}>
                              <small>[{citationIndex + 1}]</small><span>{citation.title}</span><ChevronRight />
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                  {asking && <div className="message assistant loading-message"><span><Sparkles /></span><p><i /><i /><i /></p></div>}
                </div>
              )}
            </ScrollArea>

            <form className="prompt-box" onSubmit={askAi}>
              <Textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Hỏi về tài liệu này…" aria-label="Câu hỏi cho AI" />
              <div><button type="button" onClick={() => setSettingsDialog(true)}>{settings.model}</button><Button type="submit" size="icon" aria-label="Gửi câu hỏi" disabled={!prompt.trim() || asking}><Send /></Button></div>
            </form>
            <p className="ai-disclaimer">AI có thể sai. Luôn kiểm tra phần trích dẫn.</p>
          </aside>
        )}
      </div>

      <Dialog open={sourceDialog} onOpenChange={setSourceDialog}>
        <DialogContent className="source-dialog">
          <DialogHeader><DialogTitle>Thêm nguồn tài liệu</DialogTitle><DialogDescription>Chọn vendor và nhập đường dẫn thư mục trên máy. DocAtlas chỉ đọc file, không di chuyển hay sửa nội dung gốc.</DialogDescription></DialogHeader>
          <form onSubmit={addSource} className="dialog-form">
            <label><span>Vendor</span><Select value={sourceVendor} onValueChange={(value) => changeSourceVendor(value as SourceVendor)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ericsson">Ericsson · Alex (.alx)</SelectItem><SelectItem value="zte">ZTE · eReader (.zed)</SelectItem></SelectContent></Select></label>
            <label><span>Thư mục chứa file {sourceVendor === 'ericsson' ? 'ALX' : 'ZED'}</span><Input value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder={SOURCE_DEFAULTS[sourceVendor]} /></label>
            <div className="dialog-hint"><FolderOpen /><span>DocAtlas tự nhận tất cả file đúng định dạng trong thư mục và tiếp tục lập chỉ mục ở nền.</span></div>
            <DialogFooter><Button type="button" variant="outline" onClick={() => setSourceDialog(false)}>Hủy</Button><Button type="submit" disabled={busyAction || !sourcePath.trim()}>{busyAction && <LoaderCircle className="spin" />} Kết nối & quét</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={settingsDialog} onOpenChange={setSettingsDialog}>
        <DialogContent className="settings-dialog">
          <DialogHeader><DialogTitle>Kết nối AI</DialogTitle><DialogDescription>Chọn tài khoản nhà cung cấp hoặc mô hình local. Với OpenAI, Gemini và Claude, dùng API key của tài khoản developer.</DialogDescription></DialogHeader>
          <form onSubmit={saveAiSettings} className="dialog-form">
            <label><span>Nhà cung cấp</span><Select value={settings.provider} onValueChange={(value) => changeAiProvider(value as AiSettings['provider'])}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ollama">Ollama local</SelectItem><SelectItem value="openai">OpenAI · GPT</SelectItem><SelectItem value="gemini">Google · Gemini</SelectItem><SelectItem value="anthropic">Anthropic · Claude</SelectItem><SelectItem value="openai_compatible">API tương thích OpenAI</SelectItem></SelectContent></Select></label>
            <label><span>Model</span><Input value={settings.model} onChange={(event) => setSettings((current) => ({ ...current, model: event.target.value }))} placeholder="qwen2.5:7b" /></label>
            <label><span>Địa chỉ API</span><Input value={settings.base_url} onChange={(event) => setSettings((current) => ({ ...current, base_url: event.target.value }))} placeholder="http://127.0.0.1:11434" /></label>
            {settings.provider !== 'ollama' && (
              <label><span>API key {settings.api_key_available ? '· đã kết nối' : ''}</span><Input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.api_key_available ? 'Để trống để giữ khóa hiện tại' : 'Dán API key tại đây'} autoComplete="off" /></label>
            )}
            <div className="credential-status">
              <span className={settings.api_key_available || !settings.requires_api_key ? 'connected' : 'disconnected'}>{settings.api_key_available || !settings.requires_api_key ? <CircleCheck /> : <AlertCircle />}{settings.api_key_available || !settings.requires_api_key ? 'Sẵn sàng lưu kết nối' : 'Chưa có API key'}</span>
              {settings.credential_url && settings.provider !== 'ollama' && <a href={settings.credential_url} target="_blank" rel="noreferrer">Mở trang lấy API key <ExternalLink /></a>}
            </div>
            <div className="provider-note">{settings.provider === 'ollama' ? 'Ollama xử lý nội dung ngay trên máy.' : 'Khi hỏi AI, các đoạn trích liên quan sẽ được gửi tới nhà cung cấp đã chọn.'} Khóa được mã hóa bằng tài khoản Windows hiện tại và chỉ lưu local. Gói ChatGPT/Claude web không tự cấp quyền API.</div>
            <DialogFooter>
              {settings.api_key_available && <Button type="button" variant="ghost" onClick={() => void clearAiCredential()} disabled={busyAction}>Ngắt khóa</Button>}
              <Button type="button" variant="outline" onClick={() => void testAiConnection()} disabled={testingAi || (Boolean(settings.requires_api_key) && !settings.api_key_available)}>{testingAi && <LoaderCircle className="spin" />} Kiểm tra</Button>
              <Button type="submit" disabled={busyAction}>{busyAction && <LoaderCircle className="spin" />} Lưu & kiểm tra</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader><AlertDialogTitle>Bỏ gói khỏi danh mục?</AlertDialogTitle><AlertDialogDescription>“{deleteTarget?.title}” sẽ bị xóa khỏi chỉ mục DocAtlas. File gốc trên ổ đĩa không bị xóa và sẽ xuất hiện lại khi quét nguồn.</AlertDialogDescription></AlertDialogHeader>
          <AlertDialogFooter><AlertDialogCancel>Hủy</AlertDialogCancel><AlertDialogAction variant="destructive" onClick={() => void deletePackage()} disabled={busyAction}>Bỏ khỏi danh mục</AlertDialogAction></AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </main>
  );
}
