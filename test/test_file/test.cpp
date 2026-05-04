#include<iostream>
#include<algorithm>
#include<set>
#include<vector>
using namespace std;
const int maxn = 8007;
string dot[maxn];
struct node{
    int cnt{},pos{};
    friend bool operator<(const node&a,const node&b){
        return a.cnt < b.cnt;
    }
}son[maxn];
int main(){
    int _;cin >> _;
    while (_--){
        int n;
        vector<pair<int,int>> ans;
        scanf("%d",&n);
        for(int i=1;i<=n;++i) {
            son[i].cnt = 0;
            cin >> dot[i];
            son[i].pos = i;
            dot[i] = "]" + dot[i];
        }
        for(int i=1;i<=n;++i){
            for(int j=1;j<=n;++j)
                if(dot[i][j] == '1'&&i!=j){
                    son[i].cnt++;
                }
        }
        set<int> si;
        sort(son+1,son+1+n);
        for(int i=1;i<=n;++i)
            cout << son[i].pos << ' ' << son[i].cnt << endl;
        cout <<endl;
        for(int i=1;i<=n;++i){
            if(son[i].cnt == 0)continue;
            for(int j=1;j<=n;++j) {
                if (dot[son[i].pos][j] == '1'&&son[i].pos!=j && si.find(j) == si.end()) {
                    ans.emplace_back(j, son[i].pos);
                    cout <<j<<' '<< son[i].pos<<endl;
                    si.insert(j);
                }
            }
        }
        if(ans.size() == n-1){
            puts("YES");
            for(int i=0;i<n-1;++i)
                printf("%d %d\n",ans[i].first,ans[i].second);
        }
        else
            puts("NO");
    }
    return 0;
}
