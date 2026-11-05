module door_lock #(parameter int PASSWORD_LENGTH=4, parameter int MAX_TRIALS=3)(
    input logic clk, srst,
    input logic [3:0] key_input,
    input logic key_valid, confirm, admin_override, admin_set_mode,
    input logic [PASSWORD_LENGTH*4-1:0] new_password,
    input logic new_password_valid,
    output logic door_unlock, lockout
);
    localparam IDLE=0, ENTER_PASS=1, CHECK_PASS=2, PASSWORD_OK=3,
               PASSWORD_FAIL=4, LOCKED_OUT=5, ADMIN_MODE=6;
    logic [2:0] phase;
    logic [PASSWORD_LENGTH*4-1:0] stored_password, entered;
    logic [$clog2(PASSWORD_LENGTH+1):0] digits;
    logic [$clog2(MAX_TRIALS+1):0] failures;
    always_ff @(posedge clk) begin
        if(srst) begin
            phase<=IDLE; stored_password<=1; entered<=0; digits<=0; failures<=0;
            door_unlock<=0; lockout<=0;
        end else begin
            door_unlock<=0;
            case(phase)
                IDLE: begin
                    entered<=0; digits<=0;
                    if(admin_override) begin failures<=0; phase<=admin_set_mode ? ADMIN_MODE : PASSWORD_OK; end
                    else if(confirm) phase<=PASSWORD_FAIL;
                    else if(key_valid && key_input<10) begin entered<=key_input; digits<=1; phase<=ENTER_PASS; end
                end
                ENTER_PASS: if(confirm) phase<=(digits==PASSWORD_LENGTH) ? CHECK_PASS : PASSWORD_FAIL;
                    else if(key_valid && key_input<10 && digits<PASSWORD_LENGTH) begin
                        entered<=(entered<<4)|key_input; digits<=digits+1'b1;
                    end
                CHECK_PASS: phase<=(entered==stored_password) ? PASSWORD_OK : PASSWORD_FAIL;
                PASSWORD_OK: begin door_unlock<=1; lockout<=0; failures<=0; phase<=IDLE; end
                PASSWORD_FAIL: begin
                    failures<=failures+1'b1;
                    if(failures==MAX_TRIALS-1) begin lockout<=1; phase<=LOCKED_OUT; end
                    else phase<=IDLE;
                end
                LOCKED_OUT: if(admin_override) begin failures<=0; phase<=PASSWORD_OK; end
                ADMIN_MODE: if(new_password_valid) begin stored_password<=new_password; phase<=IDLE; end
                default: phase<=IDLE;
            endcase
        end
    end
endmodule
