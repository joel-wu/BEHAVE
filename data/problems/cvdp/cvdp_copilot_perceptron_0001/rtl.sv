module perceptron_gates (
    input logic clk, rst_n,
    input logic signed [3:0] x1, x2, threshold,
    input logic learning_rate,
    input logic [1:0] gate_select,
    output logic signed [3:0] percep_w1, percep_w2, percep_bias,
    output logic [3:0] present_addr,
    output logic stop,
    output logic [2:0] input_index,
    output logic signed [3:0] y_in, y,
    output logic signed [3:0] prev_percep_wt_1, prev_percep_wt_2, prev_percep_bias
);
    localparam logic [2:0] INIT=0, COMPUTE=1, TARGET=2, UPDATE=3, CHECK=4, ADVANCE=5;
    localparam logic [17:0] MICROCODE={ADVANCE,CHECK,UPDATE,TARGET,COMPUTE,INIT};
    wire [2:0] action=present_addr<6 ? MICROCODE[3*present_addr +: 3] : INIT;
    logic signed [3:0] xa, xb, target;
    logic saved_rate;
    logic [1:0] saved_gate;
    logic epoch_changed;
    logic signed [8:0] total;
    logic signed [4:0] delta1, delta2;
    logic signed [3:0] delta_bias;
    logic positive;
    always_comb begin
        total=9'($signed(percep_bias))+9'($signed(x1))*9'($signed(percep_w1))
             +9'($signed(x2))*9'($signed(percep_w2));
        case (saved_gate)
            0: positive=(input_index==0);
            1: positive=(input_index!=3);
            2: positive=(input_index!=0);
            default: positive=(input_index==3);
        endcase
        delta1=0; delta2=0; delta_bias=0;
        if (saved_rate && y!=target) begin
            delta1=5'(xa)*5'(target); delta2=5'(xb)*5'(target); delta_bias=target;
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            percep_w1<=0; percep_w2<=0; percep_bias<=0; present_addr<=0;
            stop<=0; input_index<=0; y_in<=0; y<=0;
            prev_percep_wt_1<=0; prev_percep_wt_2<=0; prev_percep_bias<=0;
            xa<=0; xb<=0; target<=0; saved_rate<=0; saved_gate<=0; epoch_changed<=0;
        end else if (!stop) case (action)
            INIT: begin
                percep_w1<=0; percep_w2<=0; percep_bias<=0; input_index<=0;
                prev_percep_wt_1<=0; prev_percep_wt_2<=0; prev_percep_bias<=0;
                epoch_changed<=0; present_addr<=1;
            end
            COMPUTE: begin
                xa<=x1; xb<=x2; saved_rate<=learning_rate; saved_gate<=gate_select;
                y_in<=total[3:0];
                y<=total>9'($signed(threshold)) ? 4'sd1 : (total<9'($signed(threshold)) ? -4'sd1 : 4'sd0);
                present_addr<=2;
            end
            TARGET: begin target<=positive ? 4'sd1 : -4'sd1; present_addr<=3; end
            UPDATE: begin
                percep_w1<=4'(5'(percep_w1)+delta1); percep_w2<=4'(5'(percep_w2)+delta2);
                percep_bias<=percep_bias+delta_bias;
                prev_percep_wt_1<=4'(delta1); prev_percep_wt_2<=4'(delta2); prev_percep_bias<=delta_bias;
                epoch_changed<=epoch_changed || delta1!=0 || delta2!=0 || delta_bias!=0;
                present_addr<=4;
            end
            CHECK: begin
                if (input_index==3 && !epoch_changed) stop<=1;
                present_addr<=5;
            end
            ADVANCE: begin
                if (input_index==3) epoch_changed<=0;
                input_index<=(input_index+1'b1)&3; present_addr<=1;
            end
            default: ;
        endcase
    end
endmodule
