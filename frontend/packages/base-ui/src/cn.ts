import { type ClassValue, clsx } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// tailwind-merge 默认不认识 tailwind.config.js 的自定义字阶（text-mini/caption/body-sm/…），
// 会把未知 text-* 误归入「文字颜色组」：与 text-white/text-foreground 同串时按冲突删除前者
// （Button size=sm 主按钮白字丢失即此因）。将字阶键注册进 font-size 组；
// 新增字阶时需与 tailwind.config.js 的 fontSize 键两处同步。
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        {
          text: [
            'mini',
            'caption',
            'body-sm',
            'body',
            'body-lg',
            'title',
            'heading',
            'headline',
            'display',
            'display-lg',
          ],
        },
      ],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
