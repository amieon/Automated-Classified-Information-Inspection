#include <stdio.h>
int main(){
    long p;
    int i, state = 0;
    printf("please input a number\n");
    printf("input q to quit\n");
    while(scanf("%ld", &p) == 1){
        for(i = 2;i * i <= p;++i ){
            if(i * i != p){
                if( p % i == 0){
                    printf("%ld is diviable by %d and %d\n", p, i, p/i);
                    state += 1;}
            }
            else{
                printf("%ld is diviable by %d\n", p, i);
                state += 1;}
        }
        if(state == 0)
        printf("p is a premiere\n");
        if(state != 0)
        printf("p is not a premiere\n");
    }
    return 0;
}