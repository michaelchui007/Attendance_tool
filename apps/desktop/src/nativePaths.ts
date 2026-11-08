import type { NativePathSelection, SourceFile } from './types';

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

/** 使用引擎回传的原始入参关联文件，兼容不带 inputPath 的旧结果。
 *
 * Args:
 *   source: 保留规范路径与原始路径的来源描述。
 *   inputPath: 用户实际加入文件清单的路径。
 * Returns:
 *   是否对应同一个导入条目；不靠文件名或内容标识误合并路径别名。
 */
export function matchesSourceInput(source: SourceFile, inputPath: string): boolean {
  return (source.inputPath ?? source.path) === inputPath;
}

/** 冲突选择显示完整路径，避免不同文件夹的同名报表无法区分。 */
export function sourceChoiceLabel(source: SourceFile | undefined): string {
  return source ? source.name + ' — ' + (source.inputPath ?? source.path) : '来源记录缺失，请重新导入';
}

/** 找出尚无已解析来源信息的文件，防止移除时猜测其覆盖范围。 */
export function unknownCoveragePaths(inputPaths: string[], sources: SourceFile[]): string[] {
  return inputPaths.filter(path => !sources.some(source => matchesSourceInput(source, path)));
}
