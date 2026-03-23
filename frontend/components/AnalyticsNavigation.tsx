import React from 'react';
import { useRouter } from 'next/router';
import { Home, BarChart3, Camera, Settings } from 'lucide-react';

const AnalyticsNavigation: React.FC = () => {
  const router = useRouter();

  const navigationItems = [
    { icon: Home, label: 'Home', href: '/' },
    { icon: Camera, label: 'Classify', href: '/classify' },
    { icon: BarChart3, label: 'Analytics', href: '/analytics' },
    { icon: Settings, label: 'Settings', href: '/settings' }
  ];

  return (
    <nav className="bg-white shadow-sm border-b border-gray-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex items-center">
            <h1 className="text-xl font-bold text-gray-900">🍲 FlavorSnap</h1>
          </div>
          <div className="flex items-center space-x-4">
            {navigationItems.map((item) => (
              <button
                key={item.href}
                onClick={() => router.push(item.href)}
                className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  router.pathname === item.href
                    ? 'bg-blue-100 text-blue-700'
                    : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100'
                }`}
              >
                <item.icon className="w-4 h-4" />
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </nav>
  );
};

export default AnalyticsNavigation;
