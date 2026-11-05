module vending_machine (
    input logic clk,rst,item_button,
    input logic [2:0] item_selected,
    input logic [3:0] coin_input,
    input logic cancel,
    output logic dispense_item,return_change,
    output logic [4:0] item_price,change_amount,
    output logic [2:0] dispense_item_id,
    output logic error,return_money
);
    typedef enum logic [2:0] {IDLE,ITEM_SELECTION,PAYMENT_VALIDATION,
        DISPENSING_ITEM,RETURN_CHANGE,RETURN_MONEY} state_t;
    state_t state;
    logic [2:0] item;
    logic [4:0] credit;
    logic error_pending,previous_button,previous_cancel;
    wire button_rise=item_button & ~previous_button;
    wire cancel_rise=cancel & ~previous_cancel;
    wire valid_coin=(coin_input==1 || coin_input==2 || coin_input==5 || coin_input==10);
    wire [4:0] price=5*item;
    wire [5:0] total={1'b0,credit}+{2'b0,coin_input};
    always_comb begin
        dispense_item=(state==DISPENSING_ITEM);
        return_change=(state==RETURN_CHANGE);
        item_price=(state==PAYMENT_VALIDATION || state==DISPENSING_ITEM) ? price : 0;
        change_amount=(state==RETURN_CHANGE) ? credit-price : 0;
        dispense_item_id=(state==DISPENSING_ITEM) ? item : 0;
        error=(state==RETURN_MONEY && error_pending);
        return_money=(state==RETURN_MONEY && !error_pending && credit!=0);
    end
    always_ff @(posedge clk or posedge rst)begin
        if(rst)begin
            state<=IDLE;item<=0;credit<=0;error_pending<=0;
            previous_button<=0;previous_cancel<=0;
        end else begin
            previous_button<=item_button;previous_cancel<=cancel;
            if(state==RETURN_MONEY)begin
                if(error_pending)error_pending<=0;
                else begin state<=IDLE;item<=0;credit<=0;end
            end else if(state==RETURN_CHANGE)begin state<=IDLE;item<=0;credit<=0;end
            else if(state==DISPENSING_ITEM)begin
                if(credit>price)state<=RETURN_CHANGE;
                else begin state<=IDLE;item<=0;credit<=0;end
            end else if(cancel_rise)begin state<=RETURN_MONEY;error_pending<=1;end
            else if((state==IDLE || state==ITEM_SELECTION) && coin_input!=0)begin
                if(valid_coin)credit<=total[4:0];
                state<=RETURN_MONEY;error_pending<=1;
            end else case(state)
                IDLE:if(button_rise)state<=ITEM_SELECTION;
                ITEM_SELECTION:begin
                    if(item_selected>=1 && item_selected<=4)begin item<=item_selected;state<=PAYMENT_VALIDATION;end
                    else if(item_selected!=0)begin state<=RETURN_MONEY;error_pending<=1;end
                end
                PAYMENT_VALIDATION:if(coin_input!=0)begin
                    if(!valid_coin)begin state<=RETURN_MONEY;error_pending<=1;end
                    else begin credit<=total[4:0];if(total>=price)state<=DISPENSING_ITEM;end
                end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
