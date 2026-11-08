import { useEffect, useRef, useState } from 'react';
import type { WorkbookState, WorkbookStateFile } from '../types';
import { combineWorkbookStates, usableWorkbookFiles } from '../workbookState';

interface Props { files: WorkbookStateFile[]; onSoftware: () => void; onExcel: (state: WorkbookState, file: WorkbookStateFile) => void; }

/** 导入携带配置时必须选择来源，不把外部工作簿配置静默写入系统默认。 */
export default function WorkbookConfigDialog({ files, onSoftware, onExcel }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState('');
  const valid = usableWorkbookFiles(files);
  const combined = combineWorkbookStates(files);
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  const picked = valid.find(file => file.inputPath === selected);
  const chosen = combined.state || picked?.state;
  return <dialog ref={dialog} className="workbook-dialog" aria-labelledby="workbook-config-title" onCancel={event => event.preventDefault()}>
    <h2 id="workbook-config-title">选择本次统计的配置</h2>
    <p>检测到 Excel 携带的配置或修订信息。选择前不会开始计算，也不会更改软件保存的默认配置。</p>
    <div className="workbook-config-files">{files.filter(file => file.status !== 'none').map(file => <div key={file.inputPath}><strong title={file.inputPath}>{file.inputPath.split(/[\\/]/).pop()}</strong><span>{file.status === 'valid' ? '包含 ' + (file.state?.overrides.length || 0) + ' 天修订' : '配置不可用'} · {file.message || file.state?.savedAt || ''}</span></div>)}</div>
    {combined.conflict && <p className="warning-text">{combined.conflict}</p>}
    {!combined.state && valid.length > 0 && <label>选择工作簿方案<select aria-label="选择工作簿方案" value={selected} onChange={event => setSelected(event.target.value)}><option value="">请选择配置来源</option>{valid.map(file => <option key={file.inputPath} value={file.inputPath}>{file.inputPath}</option>)}</select></label>}
    {valid.length > 0 && <p className="portable-choice-note">使用 Excel 配置会替换本次会话中的计算配置和修订{combined.state ? '，一致的工作簿修订会合并' : '，仅采用所选文件中的修订，其他方案忽略'}。源文件的原始打卡仍正常导入。</p>}
    <p>使用软件配置将忽略此次检测的 Excel 配置；当前会话已生效的人工修订会保留。</p>
    <div className="dialog-actions"><button className="secondary-button" onClick={onSoftware}>使用软件配置</button><button className="primary-button" disabled={!chosen} onClick={() => { if (chosen) onExcel(chosen, picked || valid[0]); }}>使用 Excel 配置</button></div>
  </dialog>;
}
