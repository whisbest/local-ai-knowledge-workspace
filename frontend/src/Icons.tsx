type IconName = 'home' | 'folder' | 'document' | 'chat' | 'settings' | 'spark' | 'arrow' | 'check' | 'clock' | 'alert' | 'grid' | 'plus' | 'close';
const paths: Record<IconName, string> = {
  home: 'M3 10 12 3l9 7v10H3Z M9 20v-7h6v7', folder: 'M3 5h7l2 3h9v12H3Z',
  document: 'M6 3h9l4 4v14H6Z M14 3v5h5 M9 12h7 M9 16h5', chat: 'M4 4h16v13H9l-5 4Z M8 9h8 M8 13h5',
  settings: 'M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  spark: 'm12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8Z', arrow: 'M5 19 19 5 M5 5h14v14',
  check: 'm5 12 4 4L19 6', clock: 'M12 7v5l3 2', alert: 'M12 7v6 M12 17h.01',
  grid: 'M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z', plus: 'M12 5v14 M5 12h14', close: 'm6 6 12 12 M6 18 18 6',
};
export default function Icon({name, className = ''}: {name: IconName; className?: string}) {
  return <svg className={'icon ' + className} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">
    {['settings', 'clock', 'alert'].includes(name) && <circle cx="12" cy="12" r={name === 'settings' ? 4 : 9}/>}
    <path d={paths[name]}/>
  </svg>;
}
