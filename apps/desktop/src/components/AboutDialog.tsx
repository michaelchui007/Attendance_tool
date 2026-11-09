import { useEffect, useRef } from 'react';

/** 展示原生程序提供的公开信息，不占主要操作区。 */
export default function AboutDialog({ version, author, onClose }: { version: string; author: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  return <dialog ref={dialog} className="about-dialog" aria-labelledby="about-title" onCancel={onClose}><h2 id="about-title">关于考勤统计</h2><p>版本：{version || '开发版'}</p><p>作者：<strong>{author || '作者信息暂不可用'}</strong></p><p>考勤资料在本机处理，原始记录与人工修订可追溯。</p><button className="primary-button" onClick={onClose}>关闭</button></dialog>;
}
