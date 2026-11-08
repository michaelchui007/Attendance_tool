import { useEffect, useRef } from 'react';

/** 关于入口不占主要操作区，作者署名公开且可核对。 */
export default function AboutDialog({ version, onClose }: { version: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  return <dialog ref={dialog} className="about-dialog" aria-labelledby="about-title" onCancel={onClose}><h2 id="about-title">关于考勤统计</h2><p>版本：{version || '开发版'}</p><p>作者：<strong>MichaelChui</strong></p><p>考勤资料在本机处理，原始记录与人工修订可追溯。</p><button className="primary-button" onClick={onClose}>关闭</button></dialog>;
}
