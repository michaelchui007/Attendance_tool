import type { NativePathSelection } from './types';

/** 优先使用原生 UTF-16 数值序列，保留中文与代理对字符。 */
export function decodeNativePath(selection: NativePathSelection): string {
  if (selection.pathUtf16) {
    let path = '';
    for (let i = 0; i < selection.pathUtf16.length; i += 4096) path += String.fromCharCode(...selection.pathUtf16.slice(i, i + 4096));
    return path;
  }
  if (typeof selection.path === 'string') return selection.path;
  throw new Error('原生文件选择器没有返回有效路径。');
}

/** 数值路径不经过代码页，供原生命令校验与计算使用。 */
export function pathUtf16(path: string): number[] { return Array.from({ length: path.length }, (_, index) => path.charCodeAt(index)); }
