import { useEffect, useState } from 'react';
import { scanFiles, importFile, importAll } from '../lib/api';
import { cn } from '../lib/utils';
import { Upload, CheckCircle, XCircle, Loader2, FolderOpen } from 'lucide-react';

export default function Import() {
  const [files, setFiles] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [importedCount, setImportedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState<string | null>(null);
  const [importingAll, setImportingAll] = useState(false);
  const [results, setResults] = useState<Record<string, any>>({});

  async function scan() {
    setLoading(true);
    const data = await scanFiles();
    setFiles(data.files || []);
    setTotal(data.total || 0);
    setImportedCount(data.imported_count || 0);
    setLoading(false);
  }

  useEffect(() => { scan(); }, []);

  async function handleImportFile(path: string) {
    setImporting(path);
    try {
      const result = await importFile(path);
      setResults(r => ({ ...r, [path]: result }));
      // Re-scan to update status
      await scan();
    } catch (e: any) {
      setResults(r => ({ ...r, [path]: { status: 'error', message: e.message } }));
    }
    setImporting(null);
  }

  async function handleImportAll() {
    setImportingAll(true);
    try {
      const result = await importAll();
      setResults(r => ({ ...r, __all__: result }));
      await scan();
    } catch (e: any) {
      setResults(r => ({ ...r, __all__: { status: 'error', message: e.message } }));
    }
    setImportingAll(false);
  }

  const unimportedFiles = files.filter(f => !f.imported);

  return (
    <div className="p-6 space-y-5 max-w-5xl">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">Import Trade Logs</h2>
        <div className="flex items-center gap-3">
          <span className="text-sm text-text-2">
            {importedCount}/{total} files imported
          </span>
          <button onClick={scan}
            className="px-3 py-1.5 rounded-lg bg-surface-3 hover:bg-surface-4 text-sm transition-colors">
            Refresh
          </button>
        </div>
      </div>

      {/* Quick actions */}
      <div className="flex gap-3">
        <button
          onClick={handleImportAll}
          disabled={importingAll || unimportedFiles.length === 0}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-accent hover:bg-accent-2
                     text-white font-medium text-sm transition-colors disabled:opacity-50"
        >
          {importingAll ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
          Import All ({unimportedFiles.length} new)
        </button>
      </div>

      {results.__all__ && (
        <div className="bg-green-dim/20 border border-green/30 rounded-lg p-3 text-sm">
          Imported {results.__all__.imported} files.
        </div>
      )}

      {/* File list */}
      <div className="bg-surface-2 border border-border rounded-xl overflow-hidden">
        <div className="p-4 border-b border-border flex items-center gap-2">
          <FolderOpen className="w-4 h-4 text-text-3" />
          <h3 className="text-sm font-medium text-text-2">Discovered Log Files</h3>
        </div>
        <div className="divide-y divide-border/50 max-h-[600px] overflow-y-auto">
          {files.map((f) => {
            const result = results[f.path];
            const isImporting = importing === f.path;
            return (
              <div key={f.path} className="flex items-center gap-4 px-4 py-3 hover:bg-surface-3/30">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{f.date}</span>
                    <span className={cn(
                      'text-xs px-1.5 py-0.5 rounded',
                      f.is_sim ? 'bg-yellow/15 text-yellow' : 'bg-blue/15 text-blue'
                    )}>
                      {f.account}
                    </span>
                    <span className="text-xs text-text-3">{f.instance}</span>
                  </div>
                  <p className="text-xs text-text-3 truncate mt-0.5">{f.path}</p>
                </div>
                <div className="flex items-center gap-2">
                  {result && result.status === 'imported' && (
                    <span className="text-xs text-green">
                      {result.trades} trades
                    </span>
                  )}
                  {f.imported ? (
                    <CheckCircle className="w-4 h-4 text-green" />
                  ) : (
                    <button
                      onClick={() => handleImportFile(f.path)}
                      disabled={isImporting}
                      className="px-3 py-1 rounded bg-surface-4 hover:bg-border text-xs
                                 font-medium transition-colors disabled:opacity-50"
                    >
                      {isImporting ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Import'}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          {loading && (
            <div className="px-4 py-8 text-center text-text-3">
              <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />
              Scanning for log files...
            </div>
          )}
          {!loading && files.length === 0 && (
            <div className="px-4 py-8 text-center text-text-3">
              No trade activity log files found in Sierra Chart directories.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
